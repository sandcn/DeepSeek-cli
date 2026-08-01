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
