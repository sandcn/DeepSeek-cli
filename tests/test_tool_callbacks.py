"""Tests for src/core/_tool_callbacks.py — ToolCallbackChain

覆盖内容：
  1. handle_tool_calls 通过 ToolScheduler.schedule() 统一调度
  2. 结果按 schedule() 返回顺序消费
  3. dispatch_agent ParallelExecutor barrier 设置
  4. on_before / on_after 回调工厂正确性
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch

import pytest

from src.core.internal.agent._tool_callbacks import ToolCallbackChain


def _meta(parallel_safe=False, requires_terminal=False):
    """创建 mock metadata 对象"""
    m = MagicMock()
    m.parallel_safe = parallel_safe
    m.requires_terminal = requires_terminal
    return m


# ═══════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_schedule():
    """Mock ToolScheduler.default().schedule()，yield schedule AsyncMock 供测试配置"""
    with patch('src.core.internal.agent._tool_callbacks.ToolScheduler') as mock_cls:
        mock_instance = MagicMock()
        mock_instance.schedule = AsyncMock(return_value=[])
        mock_cls.default.return_value = mock_instance
        yield mock_instance.schedule


@pytest.fixture
def mock_agent():
    """创建一个 mock Agent，包含 display/display_port/capture_mgr 等必要属性"""
    agent = MagicMock()
    agent.display = MagicMock()
    agent._display_port = MagicMock()
    type(agent._display_port).is_web = PropertyMock(return_value=False)
    # 保留 _async_tool_executor 用于向后兼容，但 handle_tool_calls 不再直接使用
    agent._async_tool_executor = MagicMock()
    agent._capture_mgr = MagicMock()
    agent._event_port = MagicMock()
    agent._on_tool_completed_callbacks = []
    agent.messages = []
    agent._shared_executor = None
    return agent


@pytest.fixture
def chain(mock_agent):
    """返回 ToolCallbackChain 实例"""
    return ToolCallbackChain(mock_agent)


@pytest.fixture
def tool_calls_mixed():
    """混合工具调用 — 覆盖全部工具类型"""
    return [
        {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        {"id": "tc_1", "name": "search", "arguments": {"query": "hello"}},
        {"id": "tc_2", "name": "write_file", "arguments": {"path": "b.txt", "content": "x"}},
        {"id": "tc_3", "name": "bash", "arguments": {"command": "echo hi"}},
        {"id": "tc_4", "name": "find", "arguments": {"pattern": "*.py"}},
        {"id": "tc_5", "name": "dispatch_agent",
         "arguments": {"description": "test", "prompt": "do", "type": "execute"}},
        {"id": "tc_6", "name": "user_select",
         "arguments": {"title": "pick", "options": ["a", "b"]}},
        {"id": "tc_7", "name": "ls", "arguments": {"path": "src/"}},
        {"id": "tc_8", "name": "update_file",
         "arguments": {"path": "c.txt", "old_string": "x", "new_string": "y"}},
    ]


# ═══════════════════════════════════════════════════════════════
# ToolScheduler.schedule() 统一调度验证
# ═══════════════════════════════════════════════════════════════

class TestSchedule:
    """handle_tool_calls 通过 ToolScheduler.schedule() 统一调度"""

    @pytest.mark.asyncio
    async def test_single_tool_calls_schedule(self, chain, mock_agent, mock_schedule):
        """单工具 → ToolScheduler.default().schedule() 被调用"""
        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]
        mock_schedule.return_value = [("tc_0", "ok", True)]

        await chain.handle_tool_calls("content", calls)

        mock_schedule.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multi_tool_calls_schedule(self, chain, mock_agent, mock_schedule, tool_calls_mixed):
        """多工具 → ToolScheduler.default().schedule() 被调用"""
        mock_schedule.return_value = [
            (tc["id"], f"result_{tc['name']}", True) for tc in tool_calls_mixed
        ]

        await chain.handle_tool_calls("content", tool_calls_mixed)

        mock_schedule.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_tool_calls(self, chain, mock_agent, mock_schedule):
        """空工具列表 → schedule() 仍被调用（内部返回 []）"""
        mock_schedule.return_value = []

        await chain.handle_tool_calls("content", [])

        mock_schedule.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedule_passes_correct_kwargs(self, chain, mock_agent, mock_schedule, tool_calls_mixed):
        """schedule() 接收正确的 agent_ref / on_before / on_after / run_method"""
        captured_kwargs = {}

        async def capture_schedule(tool_calls, **kwargs):
            captured_kwargs.update(kwargs)
            return [(tc["id"], f"result_{tc['name']}", True) for tc in tool_calls]

        mock_schedule.side_effect = capture_schedule

        await chain.handle_tool_calls("content", tool_calls_mixed)

        assert captured_kwargs["agent_ref"] is mock_agent
        # run_method 是 bound method，用 __self__ + __name__ 验证
        run_method = captured_kwargs.get("run_method")
        assert run_method.__self__ is chain
        assert run_method.__name__ == "_run_tool_method"

    @pytest.mark.asyncio
    async def test_schedule_passes_tool_calls_as_first_arg(self, chain, mock_agent, mock_schedule, tool_calls_mixed):
        """schedule() 的第一个位置参数是 tool_calls 列表"""
        captured_tool_calls = None

        async def capture_schedule(tool_calls, **kwargs):
            nonlocal captured_tool_calls
            captured_tool_calls = tool_calls
            return [(tc["id"], "ok", True) for tc in tool_calls]

        mock_schedule.side_effect = capture_schedule

        await chain.handle_tool_calls("content", tool_calls_mixed)

        assert captured_tool_calls == tool_calls_mixed


# ═══════════════════════════════════════════════════════════════
# 结果顺序验证
# ═══════════════════════════════════════════════════════════════

class TestResultOrdering:
    """结果按 schedule() 返回顺序消费"""

    @pytest.mark.asyncio
    async def test_results_preserve_schedule_order(self, chain, mock_agent, mock_schedule, tool_calls_mixed):
        """schedule() 返回的结果列表按序被 _append_tool_result 消费"""
        # schedule 按原始 tool_calls 顺序返回结果
        mock_schedule.return_value = [
            (tc["id"], f"result_{tc['name']}", True) for tc in tool_calls_mixed
        ]

        await chain.handle_tool_calls("content", tool_calls_mixed)

        # 验证 _append_tool_result 按返回顺序调用
        appended_ids = [
            call_args[0][0] for call_args in
            mock_agent._append_tool_result.call_args_list
        ]
        expected_ids = [tc["id"] for tc in tool_calls_mixed]
        assert appended_ids == expected_ids

    @pytest.mark.asyncio
    async def test_failed_tools_recorded_correctly(self, chain, mock_agent, mock_schedule):
        """失败的 tool_call 被正确记录到 failed_tools"""
        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "tc_1", "name": "search", "arguments": {"query": "x"}},
        ]
        mock_schedule.return_value = [
            ("tc_0", "ok", True),
            ("tc_1", "error occurred", False),
        ]

        await chain.handle_tool_calls("content", calls)

        # 两个工具都应被 append
        assert mock_agent._append_tool_result.call_count == 2
        # tc_0 成功，tc_1 失败
        assert mock_agent._append_tool_result.call_args_list[0][0] == ("tc_0", "ok")
        assert mock_agent._append_tool_result.call_args_list[1][0] == ("tc_1", "error occurred")


# ═══════════════════════════════════════════════════════════════
# dispatch_agent ParallelExecutor 集成
# ═══════════════════════════════════════════════════════════════

class TestDispatchAgentBarrier:
    """dispatch_agent 的 ParallelExecutor barrier 设置"""

    @pytest.mark.asyncio
    async def test_barrier_setup_when_dispatch_agent_present(self, chain, mock_agent, mock_schedule):
        """有 dispatch_agent 时创建 ParallelExecutor，setup_barrier 被调用，
        且在 schedule 执行期间 _shared_executor 非 None"""
        executor_states = []  # 记录各阶段 _shared_executor 状态

        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "tc_1", "name": "dispatch_agent",
             "arguments": {"description": "a", "prompt": "p", "type": "execute"}},
        ]

        async def schedule_spy(tool_calls, **kwargs):
            executor_states.append(mock_agent._shared_executor is not None)
            return [(tc["id"], "ok", True) for tc in tool_calls]

        mock_schedule.side_effect = schedule_spy

        await chain.handle_tool_calls("content", calls)

        # schedule 执行期间 _shared_executor 应非 None
        assert executor_states[0] is True
        # finally 后 _shared_executor 应被清理
        assert mock_agent._shared_executor is None

    @pytest.mark.asyncio
    async def test_no_barrier_when_no_dispatch_agent(self, chain, mock_agent, mock_schedule):
        """没有 dispatch_agent 时不创建 ParallelExecutor"""
        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]
        mock_schedule.return_value = [("tc_0", "ok", True)]

        await chain.handle_tool_calls("content", calls)

        # _shared_executor 应为 None（未被设置过）
        assert mock_agent._shared_executor is None

    @pytest.mark.asyncio
    async def test_barrier_cleaned_on_exception(self, chain, mock_agent, mock_schedule):
        """异常时 finally 清理 _shared_executor 并释放 _all_done"""
        calls = [
            {"id": "tc_0", "name": "dispatch_agent",
             "arguments": {"description": "a", "prompt": "p", "type": "execute"}},
            {"id": "tc_1", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]

        # schedule() 抛出异常
        mock_schedule.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await chain.handle_tool_calls("content", calls)

        # 即使异常，finally 也应清理
        assert mock_agent._shared_executor is None

    @pytest.mark.asyncio
    async def test_all_done_released_on_early_failure(self, chain, mock_agent, mock_schedule):
        """schedule 异常时 _all_done.set() 被调用，防止 dispatch_agent 协程永久挂起"""
        calls = [
            {"id": "tc_0", "name": "dispatch_agent",
             "arguments": {"description": "a", "prompt": "p", "type": "execute"}},
            {"id": "tc_1", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]

        _all_done_spy = None

        async def schedule_error(tool_calls, **kwargs):
            nonlocal _all_done_spy
            if mock_agent._shared_executor is not None:
                _all_done_spy = MagicMock(wraps=mock_agent._shared_executor._all_done)
                mock_agent._shared_executor._all_done = _all_done_spy
            raise RuntimeError("boom")

        mock_schedule.side_effect = schedule_error

        with pytest.raises(RuntimeError, match="boom"):
            await chain.handle_tool_calls("content", calls)

        # _all_done.set() 应被调用
        assert _all_done_spy is not None, "ParallelExecutor 未被创建"
        _all_done_spy.set.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# 回调工厂
# ═══════════════════════════════════════════════════════════════

class TestCallbackFactory:
    """_on_before / _on_after 回调工厂"""

    @pytest.mark.asyncio
    async def test_on_before_delegates_to_method(self, chain, mock_agent, mock_schedule):
        """_on_before / _on_after 正确委托到 _on_before_tool / _on_after_tool"""
        chain._on_before_tool = MagicMock()
        chain._on_after_tool = MagicMock()

        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]
        usage = {"tool_parse_elapsed": 1.5}

        async def schedule_with_callbacks(tool_calls, **kwargs):
            on_before = kwargs.get("on_before")
            on_after = kwargs.get("on_after")
            if on_before:
                on_before(tool_calls[0], "detail_str")
            if on_after:
                on_after(tool_calls[0], "output", True)
            return [(tc["id"], "ok", True) for tc in tool_calls]

        mock_schedule.side_effect = schedule_with_callbacks

        await chain.handle_tool_calls("content", calls, usage=usage)

        # _on_before_tool 被调用，parse_elapsed 已闭包捕获
        chain._on_before_tool.assert_called_once()
        call_args = chain._on_before_tool.call_args
        assert call_args[0][0] == calls[0]  # tc
        assert call_args[0][1] == "detail_str"  # detail
        assert call_args[0][2] == 1.5  # parse_elapsed

        # _on_after_tool 也被调用
        chain._on_after_tool.assert_called_once()
        after_args = chain._on_after_tool.call_args
        assert after_args[0][0] == calls[0]  # tc
        assert after_args[0][1] == "output"
        assert after_args[0][2] is True
