"""TuiEngine 命令优先级与同批排序回归测试（步骤 2 核心修复）。

2026-08-01 ink 重构：TuiEngine → InkSession（src/tui/ink/session.py），
优先级/seq/_CRITICAL_CMDS 语义原样保留。

覆盖：
- REASONING/CONTENT 优先级提至 0，与 PhaseDone 同级（PriorityQueue seq 保序）
- 同批命令出队顺序 = 插入序：内容命令先于完成命令（修复优先级反转竞态）
- REASONING/CONTENT 不进入 _CRITICAL_CMDS（push_cmd 阻塞语义不变，满队列
  时内容命令仍以 block=False 非阻塞丢弃计数）
"""

from __future__ import annotations

import queue as _queue
from unittest.mock import MagicMock, patch

from src.tui._const import ReasoningCmd, ContentCmd, PhaseDoneCmd, RenderCommand
from src.tui.ink.session import InkSession, _get_cmd_priority, _CRITICAL_CMDS
from src.tui.app.model import AppModel


def _make_session(apply_cb=None) -> InkSession:
    return InkSession(
        model=AppModel(),
        apply_cmd=apply_cb,
        config=None,
    )


class TestEnginePriority:
    """命令优先级与同批排序（InkSession 队列层）。"""

    def test_stream_cmds_same_priority_as_phase_done(self):
        """REASONING/CONTENT 与 PHASE_DONE 优先级相同（均为 0）。"""
        assert _get_cmd_priority(ReasoningCmd(text="x")) == 0
        assert _get_cmd_priority(ContentCmd(text="x")) == 0
        assert _get_cmd_priority(PhaseDoneCmd(phase="reasoning")) == 0

    def test_critical_cmds_set_unchanged(self):
        """_CRITICAL_CMDS 集合不变（REASONING/CONTENT 不进入，阻塞语义不变）。"""
        assert RenderCommand.REASONING not in _CRITICAL_CMDS
        assert RenderCommand.CONTENT not in _CRITICAL_CMDS
        assert RenderCommand.PHASE_DONE in _CRITICAL_CMDS

    def test_same_batch_content_before_phase_done(self):
        """同批命令出队顺序保持插入序：内容命令先于完成命令。"""
        applied: list = []
        session = _make_session(apply_cb=lambda m, cmd: applied.append(cmd))

        session.push_cmd(ReasoningCmd(text="tail-thought"))
        session.push_cmd(PhaseDoneCmd(phase="reasoning"))
        session.push_cmd(ContentCmd(text="first"))
        session.push_cmd(ContentCmd(text="tail"))
        session.push_cmd(PhaseDoneCmd(phase="content"))

        # 直接排空队列（不启动线程）验证出队顺序
        drained: list = []
        while not session._cmd_queue.empty():
            _, _, cmd = session._cmd_queue.get_nowait()
            drained.append(cmd)

        cids = [c.cid for c in drained]
        assert cids == [
            RenderCommand.REASONING,
            RenderCommand.PHASE_DONE,
            RenderCommand.CONTENT,
            RenderCommand.CONTENT,
            RenderCommand.PHASE_DONE,
        ]

    def test_push_cmd_stream_commands_nonblocking(self):
        """满队列时 REASONING/CONTENT 的 put 均以 block=False 调用（不进入 _CRITICAL_CMDS）。"""
        session = _make_session()
        session._cmd_queue = MagicMock()
        session._cmd_queue.put.side_effect = _queue.Full

        session.push_cmd(ReasoningCmd(text="r"))
        session.push_cmd(ContentCmd(text="c"))

        calls = session._cmd_queue.put.call_args_list
        assert len(calls) == 2
        for call in calls:
            _, kwargs = call
            assert kwargs["block"] is False
        assert session._cmd_queue_dropped == 2
        assert session._consecutive_full == 2


class TestQueueEvictLow:
    """方向4 — 队列满 LOW 优先丢弃（腾位：新命令优先级高于 LOW 时移除队列中 LOW 命令）。"""

    def _make_session(self):
        s = _make_session()
        s._cmd_queue = _queue.PriorityQueue(maxsize=2)
        return s

    def test_high_evicts_low_when_full(self):
        """队列满 + 新命令 HIGH + 队列含 LOW → LOW 被移除、HIGH 入队。"""
        from src.tui._const import WriteLineCmd, SubagentFrameCmd, RenderCommand
        s = self._make_session()
        s.push_cmd(WriteLineCmd(text="low1"))          # LOW (3)
        s.push_cmd(WriteLineCmd(text="low2"))          # LOW (3) → 队列满
        assert s._cmd_queue.qsize() == 2
        s.push_cmd(SubagentFrameCmd(frame_lines=("h",)))  # HIGH (1) < LOW → 腾位
        assert s._cmd_queue.qsize() == 2  # 腾位后仍满（移除 1 LOW + 入队 HIGH）
        # HIGH 命令已入队
        cids = [cmd.cid for _, _, cmd in s._cmd_queue.queue]
        assert RenderCommand.SUBAGENT_FRAME in cids
        # LOW 被移除计数
        assert s._cmd_queue_dropped == 1

    def test_low_new_cmd_no_evict(self):
        """新命令本身为 LOW → 直接丢弃不腾位。"""
        from src.tui._const import WriteLineCmd
        s = self._make_session()
        s.push_cmd(WriteLineCmd(text="low1"))
        s.push_cmd(WriteLineCmd(text="low2"))          # 队列满
        s.push_cmd(WriteLineCmd(text="low3"))          # LOW → 丢弃（不腾位）
        assert s._cmd_queue.qsize() == 2
        # 队列中仍只有 2 个 LOW（未被移除）
        cids = [cmd.cid for _, _, cmd in s._cmd_queue.queue]
        assert len(cids) == 2
        assert s._cmd_queue_dropped == 1

    def test_stream_cmd_full_not_evicted(self):
        """STREAM 命令（CONTENT，优先级 0）满时走丢弃计数（不因腾位逻辑改变关键分支）。"""
        from src.tui._const import ContentCmd, WriteLineCmd
        s = self._make_session()
        s.push_cmd(WriteLineCmd(text="low1"))
        s.push_cmd(WriteLineCmd(text="low2"))          # 队列满
        s.push_cmd(ContentCmd(text="x"))               # CRITICAL/STREAM (0) → 腾位
        # CONTENT 优先级 0 < 3 → 触发腾位（移除 LOW 后入队）
        assert s._cmd_queue.qsize() == 2
        assert s._cmd_queue_dropped == 1
        cids = [cmd.cid for _, _, cmd in s._cmd_queue.queue]
        from src.tui._const import RenderCommand
        assert RenderCommand.CONTENT in cids

    def test_evict_retry_still_full_falls_back_drop(self):
        """腾位重试 put 仍满（并发竞争）→ 保持丢弃（不无限循环）。"""
        from src.tui._const import WriteLineCmd, SubagentFrameCmd
        s = self._make_session()
        s.push_cmd(WriteLineCmd(text="low1"))
        s.push_cmd(WriteLineCmd(text="low2"))          # 队列满
        # 模拟腾位后重试 put 仍满（并发竞争：另一线程腾位后立即填满）
        real_put = s._cmd_queue.put
        calls = {"n": 0}

        def flaky_put(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:  # 第 1 次为初始 put（满→Full）；第 2 次为腾位后重试 → 仍满
                raise _queue.Full
            return real_put(*a, **kw)

        s._cmd_queue.put = flaky_put
        s.push_cmd(SubagentFrameCmd(frame_lines=("h",)))
        # 腾位发生（LOW 被移除），但重试 put 仍满 → HIGH 丢弃
        assert s._cmd_queue.qsize() == 1  # 仅剩 1 个 LOW
        assert s._cmd_queue_dropped == 2  # 移除 LOW(1) + 丢弃 HIGH(1)
