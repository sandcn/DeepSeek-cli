"""InkSession 单帧全量排空回归测试（2026-10-20 用户需求）。

需求：render 线程**每帧**处理的渲染 cmd 要**无限长度**，并且要**处理完
所有**——``_drain_commands_locked`` 必须排空整个命令队列（不再按批处理上限
截断），所有命令都在同一帧的 APPLY 阶段被应用；严格优先级队列下低优先级
命令不会再被高优先级洪流饿死。

本测试覆盖：
  1. 大规模积压（5000 条）一帧全部出队、队列清空；
  2. 出队顺序严格按 ``(priority, seq)``（全量排空不改变优先级语义）；
  3. ``unfinished_tasks`` 归零、``queue.join()`` 可完成（flush 不挂起）；
  4. 空队列返回 ``(空, False, True)``；
  5. 「最新值覆盖」状态命令不因全量排空而重复/丢失，最新值生效。
"""

from __future__ import annotations

import itertools
import queue
import threading

from src.tui._config import TuiConfig
from src.tui._const import ContentCmd, ParseInfoCmd, WriteLineCmd
from src.tui.ink.session import InkSession


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


def test_single_frame_drains_entire_backlog():
    """5000 条积压在一帧内全部出队（无批处理长度上限）。"""
    s = _make_session()
    for i in range(5000):
        s.push_cmd(ContentCmd(text=f"c{i}"))
    assert s._cmd_queue.qsize() == 5000

    cmds, changed, locked = s._drain_commands_locked()

    assert locked is True and changed is True
    assert len(cmds) == 5000
    assert s._cmd_queue.qsize() == 0
    assert cmds[0].text == "c0"
    assert cmds[-1].text == "c4999"


def test_drain_all_preserves_priority_then_insert_order():
    """全量排空仍严格按 (priority, seq)：低优先级命令最后处理但不会被跳过。"""
    s = _make_session()
    for i in range(20):
        s.push_cmd(WriteLineCmd(text=f"low{i}"))
    for i in range(20):
        s.push_cmd(ContentCmd(text=f"stream{i}"))
    for i in range(5):
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=i, elapsed=0.1))

    cmds, _changed, _locked = s._drain_commands_locked()

    # PARSE_INFO 属「最新值覆盖」命令：5 次入队合并为 1 条（最新值）
    assert len(cmds) == 41
    names = [type(c).__name__ for c in cmds]
    # prio0 流式 → prio2 解析进度 → prio3 低优先级
    assert names[:20] == ["ContentCmd"] * 20
    assert names[20:21] == ["ParseInfoCmd"]
    assert names[21:] == ["WriteLineCmd"] * 20
    assert [c.text for c in cmds[:20]] == [f"stream{i}" for i in range(20)]
    assert cmds[20].tokens == 4
    assert [c.text for c in cmds[21:]] == [f"low{i}" for i in range(20)]


def test_unfinished_tasks_zero_and_join_returns():
    """全量排空后 ``unfinished_tasks`` 归零，``queue.join()`` 立即完成。"""
    s = _make_session()
    for i in range(300):
        s.push_cmd(ContentCmd(text=f"c{i}"))
    s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=9, elapsed=0.9))

    s._drain_commands_locked()

    assert s._cmd_queue.qsize() == 0
    assert s._cmd_queue.unfinished_tasks == 0
    waiter = threading.Thread(target=s._cmd_queue.join, daemon=True)
    waiter.start()
    waiter.join(timeout=2.0)
    assert not waiter.is_alive(), "unfinished_tasks 未归零（queue.join 挂起）"


def test_empty_queue_returns_no_change():
    """空队列：无命令、无变更，锁正常获取。"""
    s = _make_session()
    cmds, changed, locked = s._drain_commands_locked()
    assert cmds == []
    assert changed is False
    assert locked is True


def test_coalesced_state_cmd_latest_value_survives_full_drain():
    """「最新值覆盖」状态命令在全量排空后只出现一次且为最新值。"""
    s = _make_session()
    for i in range(100):
        s.push_cmd(ContentCmd(text=f"c{i}"))
    for tokens in (1, 50, 900):
        s.push_cmd(ParseInfoCmd(tool_names="Write", tokens=tokens, elapsed=0.1))
    assert s._cmd_queue.qsize() == 101  # 进度命令已合并为一条

    cmds, _changed, _locked = s._drain_commands_locked()

    assert len(cmds) == 101
    picked = [c for c in cmds if type(c).__name__ == "ParseInfoCmd"]
    assert len(picked) == 1
    assert picked[0].tokens == 900
    assert s._cmd_queue.qsize() == 0
