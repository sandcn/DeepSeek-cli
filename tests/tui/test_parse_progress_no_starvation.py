"""解析进度行（``⠹ Write 25t 0.11s``）卡住回归测试（2026-09-20 用户报障）。

用户报障：调用 write 写较大文件时，解析进度行显示 ``⠹ Write 25t
0.11s`` 后**数字长时间不再变化**（界面仍在刷新、spinner 在转，工具参数数据
仍在传），过一会才自行恢复。

根因（命令队列优先级饥饿）：模型高速流式输出时每个 tool_calls 增量 chunk 都
产生一条 prio0（CRITICAL/STREAM）内容/阶段命令（``MainPhaseCmd`` 每 chunk
一条，实测单次解析上千条完全幂等的重复命令）。修复前的有界批处理（每帧最多
50 条）+ 严格优先级队列下，prio0 命令积压 ≥ 批处理上限后，prio2 的
``ParseInfoCmd``（解析进度行）**再也进不了任何批次**——渲染帧持续推进
（spinner 在转）而进度行 token 数/耗时数字长期不刷新；积压消化后跳变恢复
（「过一会自己恢复」）。

修复（两层，均在 TUI 命令队列底层）：
  1. **「最新值覆盖」命令合并**（``_SessionQueueMixin.push_cmd``）：PARSE_INFO/
     BG_BASH_COUNT 这类**状态快照**命令在队列中同类未消费时**就地替换**（保持
     ``(priority, seq)`` 位置与堆序、不新增条目）——队列中恒至多一条且为最新
     值，且替换先于 put 执行（队列满时最新进度仍能落地）。
  2. **每帧全量排空**（``InkSession._drain_commands_locked``）：批处理长度不设
     上限（2026-10-20 用户需求「每帧处理的渲染 cmd 要无限长度，并且要处理完
     所有」）——本帧排空队列全部命令，低优先级命令不会再被 prio0 洪流饿死，
     进度行每帧刷新。

本测试覆盖：合并语义（最新值/位置/顺序/unfinished_tasks 一致性）、队列满时
最新进度不丢、每帧全量排空（无长度上限/全部处理完/优先级顺序）、以及正常无
积压场景零行为变化。
"""

from __future__ import annotations

import itertools
import queue
import threading

import pytest

from src.tui._config import TuiConfig
from src.tui._const import (
    ContentCmd,
    MainPhaseCmd,
    ParseInfoCmd,
    WriteLineCmd,
    BgBashCountCmd,
    _CLEAR_PARSE_LINE,
)
from src.tui.ink._cmd_priority import _COALESCE_CMDS
from src.tui.ink.session import InkSession
from src.tui._const import RenderCommand


def _make_session(maxsize: int = 10000) -> InkSession:
    """构造仅含队列依赖的 InkSession 桩（不启动渲染线程/无终端）。"""
    cfg = TuiConfig.defaults().with_overrides(cmd_queue_maxsize=maxsize)
    s = object.__new__(InkSession)
    s._cmd_queue = queue.PriorityQueue(maxsize=cfg.cmd_queue_maxsize)
    s._cmd_seq = itertools.count()
    s._cmd_event = threading.Event()
    s._consecutive_full = 0
    s._cmd_queue_dropped = 0
    s._render_running = True
    s._config = cfg
    s._write_emergency = lambda *a, **k: None  # type: ignore[assignment]
    return s


def _drain_all(session: InkSession) -> list:
    """按批处理语义逐帧排空队列（返回命令顺序列表）。"""
    out: list = []
    while not session._cmd_queue.empty():
        cmds, _changed, locked = session._drain_commands_locked()
        assert locked is True
        if not cmds:
            break
        out.extend(cmds)
    return out


def _queue_items(session: InkSession) -> list:
    return list(session._cmd_queue.queue)


# ═══════════════════════════════════════════════════════════
# 1. 合并语义（「最新值覆盖」）
# ═══════════════════════════════════════════════════════════

class TestCoalesce:
    """PARSE_INFO / BG_BASH_COUNT 队列内合并。"""

    def test_coalesce_keeps_single_latest_parse_info(self):
        """重复入队只保留一条，且为**最新值**。"""
        s = _make_session()
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=25, elapsed=0.11))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=120, elapsed=0.52))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=900, elapsed=3.10))
        assert s._cmd_queue.qsize() == 1
        item = _queue_items(s)[0]
        assert item[2].tokens == 900
        assert item[2].elapsed == pytest.approx(3.10)

    def test_coalesce_preserves_first_seq_and_priority(self):
        """替换保持首入队 ``(priority, seq)``——堆序与同批插入序不变。"""
        s = _make_session()
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=1, elapsed=0.1))
        first = _queue_items(s)[0]
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=2, elapsed=0.2))
        second = _queue_items(s)[0]
        assert second[0] == first[0]
        assert second[1] == first[1]

    def test_coalesce_preserves_other_command_order(self):
        """合并不改变其它命令的相对顺序（阶段命令仍先出队）。"""
        s = _make_session()
        s.push_cmd(MainPhaseCmd(phase="parsing"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=10, elapsed=0.1))
        s.push_cmd(ContentCmd(text="hello"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=99, elapsed=0.9))
        cmds = _drain_all(s)
        assert [type(c).__name__ for c in cmds] == [
            "MainPhaseCmd", "ContentCmd", "ParseInfoCmd",
        ]
        assert cmds[-1].tokens == 99

    def test_coalesce_unfinished_tasks_consistent(self):
        """条目数不变 ⇒ ``unfinished_tasks`` 一致，``queue.join`` 可完成。"""
        s = _make_session()
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=1, elapsed=0.1))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=2, elapsed=0.2))
        assert s._cmd_queue.unfinished_tasks == 1
        drained = _drain_all(s)
        assert len(drained) == 1
        waiter = threading.Thread(target=s._cmd_queue.join, daemon=True)
        waiter.start()
        waiter.join(timeout=2.0)
        assert not waiter.is_alive(), "unfinished_tasks 不一致（queue.join 挂起）"

    def test_coalesce_replace_when_queue_full(self):
        """队列满且已有同类命令时，最新进度**替换**而非丢弃。"""
        s = _make_session(maxsize=1)
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=25, elapsed=0.11))
        assert s._cmd_queue.qsize() == 1
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=400, elapsed=1.40))
        assert s._cmd_queue.qsize() == 1
        assert s._cmd_queue_dropped == 0
        assert _queue_items(s)[0][2].tokens == 400

    def test_coalesce_covers_bg_bash_count(self):
        """后台任务计数同属「最新值覆盖」集合（合并 + 只保留最新）。"""
        assert RenderCommand.BG_BASH_COUNT in _COALESCE_CMDS
        s = _make_session()
        s.push_cmd(BgBashCountCmd(count=1, subagent_count=0))
        s.push_cmd(BgBashCountCmd(count=3, subagent_count=1))
        assert s._cmd_queue.qsize() == 1
        assert _queue_items(s)[0][2].count == 3

    def test_clear_parse_line_replaces_pending_progress(self):
        """解析完成清除命令替换掉待消费的进度命令（最终状态 = 清除）。"""
        s = _make_session()
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=25, elapsed=0.11))
        s.push_cmd(ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))
        assert s._cmd_queue.qsize() == 1
        assert _queue_items(s)[0][2].tokens == _CLEAR_PARSE_LINE

    def test_non_state_commands_not_coalesced(self):
        """非状态类命令不合并（内容命令逐条保留，零丢内容）。"""
        s = _make_session()
        for i in range(5):
            s.push_cmd(ContentCmd(text=f"c{i}"))
        s.push_cmd(WriteLineCmd(text="log"))
        assert s._cmd_queue.qsize() == 6


# ═══════════════════════════════════════════════════════════
# 2. 每帧全量排空（无批处理长度上限，处理完所有命令）
# ═══════════════════════════════════════════════════════════

class TestDrainAll:
    """``_drain_commands_locked`` 每帧排空队列全部命令（无长度上限）。"""

    def test_state_cmd_not_starved_by_prio0_flood(self):
        """prio0 洪流积压时，本帧仍处理全部命令（进度命令不丢、值最新）。"""
        s = _make_session()
        for i in range(200):
            s.push_cmd(ContentCmd(text=f"c{i}"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=42, elapsed=4.2))
        cmds, changed, locked = s._drain_commands_locked()
        assert locked and changed
        assert len(cmds) == 201
        assert s._cmd_queue.qsize() == 0
        assert [type(c).__name__ for c in cmds].count("ParseInfoCmd") == 1
        assert cmds[-1].tokens == 42

    def test_drain_all_keeps_unfinished_tasks_consistent(self):
        """全量排空后 ``unfinished_tasks`` 归零（flush/join 不挂）。"""
        s = _make_session()
        for i in range(120):
            s.push_cmd(ContentCmd(text=f"c{i}"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=7, elapsed=0.7))
        before = s._cmd_queue.qsize()
        cmds, _c, _l = s._drain_commands_locked()
        assert len(cmds) == before == 121
        assert s._cmd_queue.qsize() == 0
        assert s._cmd_queue.unfinished_tasks == 0
        waiter = threading.Thread(target=s._cmd_queue.join, daemon=True)
        waiter.start()
        waiter.join(timeout=2.0)
        assert not waiter.is_alive(), "unfinished_tasks 未归零（queue.join 挂起）"

    def test_drain_all_orders_by_priority_then_seq(self):
        """全量出队仍严格按 (priority, seq) 顺序（低优先级最后处理）。"""
        s = _make_session()
        s.push_cmd(WriteLineCmd(text="low"))
        s.push_cmd(ContentCmd(text="prio0"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=1, elapsed=0.1))
        cmds, _c, _l = s._drain_commands_locked()
        assert [type(c).__name__ for c in cmds] == [
            "ContentCmd", "ParseInfoCmd", "WriteLineCmd",
        ]

    def test_drain_all_after_multiple_batches_kept_order(self):
        """多批注入后一次排空仍保持全局 (priority, seq) 顺序。"""
        s = _make_session()
        for i in range(30):
            s.push_cmd(ContentCmd(text=f"c{i}"))
        for i in range(20):
            s.push_cmd(WriteLineCmd(text=f"w{i}"))
        cmds, _c, _l = s._drain_commands_locked()
        assert len(cmds) == 50
        assert [c.text for c in cmds[:30]] == [f"c{i}" for i in range(30)]
        assert [c.text for c in cmds[30:]] == [f"w{i}" for i in range(20)]

    def test_empty_queue_returns_no_change(self):
        """空队列：无命令、无变更，锁正常获取。"""
        s = _make_session()
        cmds, changed, locked = s._drain_commands_locked()
        assert cmds == [] and changed is False and locked is True

    def test_normal_streaming_frame_unaffected(self):
        """常规流式帧（少量命令）行为不变：按优先级全量出队。"""
        s = _make_session()
        s.push_cmd(MainPhaseCmd(phase="parsing"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=10, elapsed=0.1))
        cmds, changed, locked = s._drain_commands_locked()
        assert locked and changed
        assert [type(c).__name__ for c in cmds] == ["MainPhaseCmd", "ParseInfoCmd"]

    def test_drain_all_survives_queue_keep_content_drain(self):
        """``_drain_queue_safe(keep_content=True)`` 保留进度命令后仍可被处理。"""
        s = _make_session()
        for i in range(80):
            s.push_cmd(ContentCmd(text=f"c{i}"))
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=33, elapsed=3.3))
        s.push_cmd(WriteLineCmd(text="external"))
        dropped = s._drain_queue_safe(keep_content=True)
        assert dropped == 1  # 仅 WRITE_LINE 被丢弃
        assert s._cmd_queue.qsize() == 81
        cmds, _c, _l = s._drain_commands_locked()
        assert len(cmds) == 81
        assert cmds[-1].tokens == 33
        assert s._cmd_queue.qsize() == 0
