"""PhaseDone 发布去重助手单测 — StreamContext.publish_phase_done_once。

覆盖：
- 助手幂等（同 phase 首次发布 True、二次 False）
- 非去重 phase（segment_end）每次发布且不置位
- label 为空字符串时发布空 label（与现有一致）
- ContentHandler 首次 content 发布 PhaseDone("reasoning") 恰一次（防御性重入不重复）
- ToolCallsHandler 首次工具调用且 content_full 时发布 PhaseDone("content") 恰一次
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.api.stream.context import StreamContext
from src.api.stream.handlers.content import ContentHandler
from src.api.stream.handlers.tool_calls import ToolCallsHandler


class TestPhaseDoneDedup:
    """StreamContext.publish_phase_done_once 去重语义。"""

    def test_helper_idempotent(self):
        """同 phase 首次发布返回 True、二次返回 False 且 publish_event 仅调用 1 次。"""
        ctx = StreamContext("m", None, "main", False)
        with patch("src.api.stream.context.publish_event") as mock_publish:
            assert ctx.publish_phase_done_once("reasoning") is True
            assert ctx.publish_phase_done_once("reasoning") is False
        mock_publish.assert_called_once_with(
            "PhaseDoneEvent", label="main", phase="reasoning",
        )

    def test_helper_label_empty(self):
        """label 为空字符串时发布空 label（与现有一致）。"""
        ctx = StreamContext("m", None, "", False)
        with patch("src.api.stream.context.publish_event") as mock_publish:
            assert ctx.publish_phase_done_once("content") is True
        mock_publish.assert_called_once_with(
            "PhaseDoneEvent", label="", phase="content",
        )

    def test_helper_segment_end_not_deduped(self):
        """非去重 phase（segment_end）每次发布且不置位。"""
        ctx = StreamContext("m", None, "main", False)
        with patch("src.api.stream.context.publish_event") as mock_publish:
            assert ctx.publish_phase_done_once("segment_end") is True
            assert ctx.publish_phase_done_once("segment_end") is True
        assert mock_publish.call_count == 2

    def test_content_handler_publishes_once(self):
        """ContentHandler 首次 content 发布 PhaseDone("reasoning") 恰一次。

        防御性重入（第二次 handle 前恢复 is_reasoning）由去重助手幂等挡住，
        不重复发布。
        """
        ctx = StreamContext("m", None, "main", False)
        handler = ContentHandler()
        # 同时抑制 _base.publish_event：buffer() 的 ContentChunkEvent 不走全局
        # DisplayEventBus 单例（避免跨测试污染），断言只针对 context.publish_event。
        with patch("src.api.stream.context.publish_event") as mock_ctx_pub, \
             patch("src.api.stream.handlers._base.publish_event"):
            handler.handle(ctx, "hello")
            ctx.is_reasoning = True  # 模拟防御性分支重入
            handler.handle(ctx, "world")
        phase_done_calls = [
            call for call in mock_ctx_pub.call_args_list
            if call.args[0] == "PhaseDoneEvent"
        ]
        assert len(phase_done_calls) == 1
        assert phase_done_calls[0].kwargs == {"label": "main", "phase": "reasoning"}

    async def test_tool_calls_handler_publishes_once(self):
        """ToolCallsHandler 首次工具调用且 content_full 时发布 PhaseDone("content") 恰一次。"""
        ctx = StreamContext("m", None, "main", False)
        ctx.content_full = "some content"
        # mock tracker：避免真实 ToolParseTracker 的 Timer/display 依赖
        ctx.tracker = MagicMock()
        ctx.tracker.started = False
        ctx.tracker.start = AsyncMock()
        handler = ToolCallsHandler()
        delta = [{"index": 0, "function": {"name": "tool_a", "arguments": "{}"}}]
        with patch("src.api.stream.context.publish_event") as mock_publish:
            await handler.handle(ctx, delta)
            await handler.handle(ctx, delta)
        phase_done_calls = [
            call for call in mock_publish.call_args_list
            if call.args[0] == "PhaseDoneEvent"
        ]
        assert len(phase_done_calls) == 1
        assert phase_done_calls[0].kwargs == {"label": "main", "phase": "content"}
