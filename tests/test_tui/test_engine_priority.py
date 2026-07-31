"""TuiEngine 命令优先级与同批排序回归测试（步骤 2 核心修复）。

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
from src.tui._renderer._engine import TuiEngine, _get_cmd_priority, _CRITICAL_CMDS


class TestEnginePriority:
    """TuiEngine 命令优先级与同批排序。"""

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
        renderer = MagicMock()
        bottom_bar = MagicMock()
        bottom_bar.is_active = False
        engine = TuiEngine(renderer, bottom_bar)
        # 单条 render() 路径（不批量）
        renderer._is_batchable.return_value = False

        engine.push_cmd(ReasoningCmd(text="tail-thought"))
        engine.push_cmd(PhaseDoneCmd(phase="reasoning"))
        engine.push_cmd(ContentCmd(text="first"))
        engine.push_cmd(ContentCmd(text="tail"))
        engine.push_cmd(PhaseDoneCmd(phase="content"))

        class _FakeLock:
            def __enter__(self):
                return True
            def __exit__(self, *a):
                return False

        with patch(
            "src.tui._renderer._engine._try_acquire_output_lock",
            return_value=_FakeLock(),
        ):
            engine._drain_queue()

        cids = [call.args[0].cid for call in renderer.render.call_args_list]
        assert cids == [
            RenderCommand.REASONING,
            RenderCommand.PHASE_DONE,
            RenderCommand.CONTENT,
            RenderCommand.CONTENT,
            RenderCommand.PHASE_DONE,
        ]

    def test_push_cmd_stream_commands_nonblocking(self):
        """满队列时 REASONING/CONTENT 的 put 均以 block=False 调用（不进入 _CRITICAL_CMDS）。"""
        renderer = MagicMock()
        bottom_bar = MagicMock()
        engine = TuiEngine(renderer, bottom_bar)
        engine._cmd_queue = MagicMock()
        engine._cmd_queue.put.side_effect = _queue.Full

        engine.push_cmd(ReasoningCmd(text="r"))
        engine.push_cmd(ContentCmd(text="c"))

        calls = engine._cmd_queue.put.call_args_list
        assert len(calls) == 2
        for call in calls:
            _, kwargs = call
            assert kwargs["block"] is False
        assert engine._cmd_queue_dropped == 2
        assert engine._consecutive_full == 2
