"""测试模块 A — SubAgent dispatch_agent 支持

测试 SubAgent 的 _shared_executor 机制、get_config_port() 委托、
dispatch_agent 检测与清理逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.subagent import SubAgent


# ── 辅助函数 ─────────────────────────────────────────────

def _make_mock_parent():
    """创建一个最小 mock 父 Agent，包含 dispatch_agent 所需的所有端口。"""
    parent = MagicMock()
    parent.model = "test-model"
    parent._event_port = MagicMock()

    # ToolRegistry mock
    mock_registry = MagicMock()
    mock_registry.get_schemas.return_value = []
    parent.get_tool_registry.return_value = mock_registry

    # PromptBuilderPort mock
    mock_prompt_port = MagicMock()
    mock_prompt_port.build_execute_agent_system_prompt.return_value = []
    mock_prompt_port.build_subagent_prompt.return_value = []
    parent.get_prompt_builder_port.return_value = mock_prompt_port

    # ConfigPort mock
    mock_config = MagicMock()
    parent.get_config_port.return_value = mock_config

    parent._async_model_port = None
    return parent


def _make_subagent(parent=None, agent_type="execute"):
    """创建一个最小 SubAgent 实例（不真实调用模型）。"""
    if parent is None:
        parent = _make_mock_parent()
    return SubAgent(
        label="test-agent",
        description="Test SubAgent",
        prompt="test prompt",
        parent_agent=parent,
        agent_type=agent_type,
    )


# ── 测试类 ───────────────────────────────────────────────

class TestSubAgentDispatchAgentSupport:
    """测试 SubAgent 的 dispatch_agent 支持机制"""

    def test_subagent_has_shared_executor_attr(self):
        """验证 SubAgent 初始化后 _shared_executor 和 _display_port 为 None"""
        sa = _make_subagent()

        assert hasattr(sa, '_shared_executor')
        assert sa._shared_executor is None
        assert hasattr(sa, '_display_port')
        assert sa._display_port is None

    def test_subagent_get_config_port_delegates_to_parent(self):
        """验证 get_config_port() 委托给 parent.get_config_port()"""
        parent = _make_mock_parent()
        mock_config = parent.get_config_port.return_value

        sa = _make_subagent(parent=parent)

        result = sa.get_config_port()
        assert result is mock_config
        parent.get_config_port.assert_called_once()

    def test_subagent_get_config_port_no_parent_attr(self):
        """验证 parent 无 get_config_port 时抛出 AttributeError"""
        parent = _make_mock_parent()
        # 移除 get_config_port 方法
        del parent.get_config_port

        sa = _make_subagent(parent=parent)

        with pytest.raises(AttributeError):
            sa.get_config_port()

    @pytest.mark.asyncio
    async def test_subagent_detect_dispatch_agent_creates_executor(self):
        """验证 _handle_tool_calls 检测到 dispatch_agent 时创建 _shared_executor"""
        sa = _make_subagent()

        # Mock BaseAgent 方法
        sa._append_assistant_message = MagicMock()
        sa._append_tool_result = MagicMock()

        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True

        with patch('src.core.parallel_executor.ParallelExecutor', return_value=mock_executor) as mock_pe:
            with patch('src.core.subagent.ToolScheduler') as mock_ts:
                mock_scheduler = MagicMock()
                mock_scheduler.schedule = AsyncMock(return_value=[])
                mock_ts.default.return_value = mock_scheduler

                tool_calls = [
                    {"name": "dispatch_agent", "id": "call_1", "arguments": "{}"},
                ]

                await sa._handle_tool_calls("test content", tool_calls)

                # 验证 ParallelExecutor 以正确参数创建
                mock_pe.assert_called_once_with(sa, is_web=False)
                # 验证 setup_barrier 被调用
                mock_executor.setup_barrier.assert_called_once_with(1)
                # 验证 finally 中 barrier 被释放 (_all_done.set)
                mock_executor._all_done.set.assert_called_once()
                # 验证 finally 后 _shared_executor 被清理
                assert sa._shared_executor is None

    @pytest.mark.asyncio
    async def test_subagent_no_dispatch_agent_shared_executor_none(self):
        """验证没有 dispatch_agent 时 _shared_executor 保持 None"""
        sa = _make_subagent()

        sa._append_assistant_message = MagicMock()
        sa._append_tool_result = MagicMock()

        with patch('src.core.subagent.ToolScheduler') as mock_ts:
            mock_scheduler = MagicMock()
            mock_scheduler.schedule = AsyncMock(return_value=[])
            mock_ts.default.return_value = mock_scheduler

            tool_calls = [
                {"name": "read_file", "id": "call_1", "arguments": "{}"},
                {"name": "search", "id": "call_2", "arguments": "{}"},
            ]

            await sa._handle_tool_calls("test content", tool_calls)

            # 没有 dispatch_agent，_shared_executor 应为 None
            assert sa._shared_executor is None

    @pytest.mark.asyncio
    async def test_subagent_multiple_dispatch_agent_barrier_count(self):
        """验证多个 dispatch_agent 时 setup_barrier 传入正确计数"""
        sa = _make_subagent()

        sa._append_assistant_message = MagicMock()
        sa._append_tool_result = MagicMock()

        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True

        with patch('src.core.parallel_executor.ParallelExecutor', return_value=mock_executor):
            with patch('src.core.subagent.ToolScheduler') as mock_ts:
                mock_scheduler = MagicMock()
                mock_scheduler.schedule = AsyncMock(return_value=[])
                mock_ts.default.return_value = mock_scheduler

                tool_calls = [
                    {"name": "dispatch_agent", "id": "call_1", "arguments": "{}"},
                    {"name": "dispatch_agent", "id": "call_2", "arguments": "{}"},
                    {"name": "dispatch_agent", "id": "call_3", "arguments": "{}"},
                ]

                await sa._handle_tool_calls("test content", tool_calls)

                # setup_barrier 被调用且 count = 3
                mock_executor.setup_barrier.assert_called_once_with(3)

    @pytest.mark.asyncio
    async def test_subagent_dispatch_agent_cleanup_on_exception(self):
        """验证 schedule() 抛出异常时 _shared_executor 被正确清理"""
        sa = _make_subagent()

        sa._append_assistant_message = MagicMock()
        sa._append_tool_result = MagicMock()

        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True

        with patch('src.core.parallel_executor.ParallelExecutor', return_value=mock_executor):
            with patch('src.core.subagent.ToolScheduler') as mock_ts:
                mock_scheduler = MagicMock()
                mock_scheduler.schedule = AsyncMock(side_effect=RuntimeError("工具调度失败"))
                mock_ts.default.return_value = mock_scheduler

                tool_calls = [
                    {"name": "dispatch_agent", "id": "call_1", "arguments": "{}"},
                ]

                with pytest.raises(RuntimeError, match="工具调度失败"):
                    await sa._handle_tool_calls("test content", tool_calls)

                # 验证异常后 barrier 被释放
                mock_executor._all_done.set.assert_called_once()
                # 验证 _shared_executor 被清理
                assert sa._shared_executor is None

    @pytest.mark.asyncio
    async def test_subagent_dispatch_agent_cleanup_on_cancelled(self):
        """验证 asyncio.CancelledError 时 _shared_executor 被正确清理"""
        sa = _make_subagent()

        sa._append_assistant_message = MagicMock()
        sa._append_tool_result = MagicMock()

        mock_executor = MagicMock()
        mock_executor.is_batch_mode = True

        import asyncio

        with patch('src.core.parallel_executor.ParallelExecutor', return_value=mock_executor):
            with patch('src.core.subagent.ToolScheduler') as mock_ts:
                mock_scheduler = MagicMock()
                mock_scheduler.schedule = AsyncMock(side_effect=asyncio.CancelledError())
                mock_ts.default.return_value = mock_scheduler

                tool_calls = [
                    {"name": "dispatch_agent", "id": "call_1", "arguments": "{}"},
                ]

                with pytest.raises(asyncio.CancelledError):
                    await sa._handle_tool_calls("test content", tool_calls)

                # 验证 CancelledError 后 barrier 被释放
                mock_executor._all_done.set.assert_called_once()
                # 验证 _shared_executor 被清理
                assert sa._shared_executor is None
