"""测试 Pipeline Bug 修复 — P0-1, P1-2"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.pipeline import Pipeline, PipelineContext


class TestCancelledErrorStateTransition:
    """P0-1: CancelledError 路径触发 on_round_complete 钩子"""

    @pytest.fixture
    def pipeline(self):
        return Pipeline()

    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent._capture_mgr = AsyncMock()
        agent._capture_mgr.cleanup = AsyncMock()
        return agent

    @pytest.fixture
    def ctx(self, mock_agent):
        ctx = PipelineContext(mock_agent)
        return ctx

    @pytest.mark.asyncio
    async def test_cancelled_error_triggers_on_round_complete(self, pipeline, ctx):
        """P0-1: CancelledError 时 on_round_complete 钩子被调用"""
        on_round_complete_mock = AsyncMock()
        pipeline.use_async(on_round_complete_mock)

        # 让 _execute_model_call_async 抛出 CancelledError
        with patch.object(
            pipeline, '_execute_model_call_async',
            side_effect=asyncio.CancelledError("模拟取消"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await pipeline.run_round_async(ctx)

        # 验证 on_round_complete 被调用
        on_round_complete_mock.on_round_complete.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_cancelled_error_sets_interrupted_and_checkpoint(self, pipeline, ctx):
        """P0-1: CancelledError 路径设置 interrupted=True, checkpoint_requested=True"""
        with patch.object(
            pipeline, '_execute_model_call_async',
            side_effect=asyncio.CancelledError("模拟取消"),
        ):
            with pytest.raises(asyncio.CancelledError):
                await pipeline.run_round_async(ctx)

        assert ctx.interrupted is True
        assert ctx.round_complete is True
        assert ctx.checkpoint_requested is True
        assert isinstance(ctx.error, asyncio.CancelledError)


class TestInterruptToolCalls:
    """P1-2: 中断时 tool_calls 不写入 assistant 消息"""

    @pytest.fixture
    def pipeline(self):
        return Pipeline()

    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent._capture_mgr = AsyncMock()
        agent._capture_mgr.cleanup = AsyncMock()
        agent._append_assistant_msg = MagicMock()
        agent._append_assistant_message = MagicMock()
        agent._get_active_tools = MagicMock(return_value=[])
        agent.model = "test-model"
        agent.display = None
        agent.messages = []
        return agent

    @pytest.mark.asyncio
    async def test_interrupt_with_tool_calls_no_tool_calls_in_msg(self, pipeline, mock_agent):
        """P1-2: 中断时有 tool_calls 时，仅调用 _append_assistant_msg（不含 tool_calls）"""
        ctx = PipelineContext(mock_agent)

        # patch is_interrupted_async at the source module
        with patch('src.api.interrupt_async.is_interrupted_async', AsyncMock(return_value=True)):
            call_model_async = AsyncMock(return_value=("", "中断内容", {}, [{"id": "tc1", "name": "bash", "arguments": {"command": "ls"}}]))
            with patch.object(mock_agent, '_call_model_async', call_model_async):
                await pipeline._execute_model_call_async(ctx)

        # 验证只调用了 _append_assistant_msg（不含 tool_calls）
        mock_agent._append_assistant_msg.assert_called_once()
        mock_agent._append_assistant_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_interrupt_without_tool_calls_normal_path(self, pipeline, mock_agent):
        """P1-2: 没有 tool_calls 时正常调用 _append_assistant_msg"""
        ctx = PipelineContext(mock_agent)

        with patch('src.api.interrupt_async.is_interrupted_async', AsyncMock(return_value=False)):
            call_model_async = AsyncMock(return_value=("推理", "正常内容", {}, []))
            with patch.object(mock_agent, '_call_model_async', call_model_async):
                await pipeline._execute_model_call_async(ctx)

        mock_agent._append_assistant_msg.assert_called_once_with("正常内容", "推理")
