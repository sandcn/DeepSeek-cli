"""Tests for src/core/_tool_callbacks.py — ToolCallbackChain

覆盖内容：
  1. _READ_ONLY_TOOLS 常量正确性
  2. handle_tool_calls 四波分类正确性（通过 mock execute_async 验证波次顺序）
  3. 结果按原始 tool_calls 顺序重建
  4. results_map 缺失键的降级处理
  5. dispatch_agent ParallelExecutor barrier 设置
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, PropertyMock

import pytest

from src.core._tool_callbacks import ToolCallbackChain, _READ_ONLY_TOOLS


# ═══════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_agent():
    """创建一个 mock Agent，包含 display/registry/async_tool_executor 等必要属性"""
    agent = MagicMock()
    agent.display = MagicMock()
    agent._display_port = MagicMock()
    type(agent._display_port).is_web = PropertyMock(return_value=False)
    agent._async_tool_executor = MagicMock()
    agent._async_tool_executor.execute_async = AsyncMock(return_value=[])
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
    """混合工具调用 — 覆盖全部四类工具"""
    return [
        {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        {"id": "tc_1", "name": "search", "arguments": {"query": "hello"}},
        {"id": "tc_2", "name": "write_file", "arguments": {"path": "b.txt", "content": "x"}},
        {"id": "tc_3", "name": "bash", "arguments": {"command": "echo hi"}},
        {"id": "tc_4", "name": "find", "arguments": {"pattern": "*.py"}},
        {"id": "tc_5", "name": "dispatch_agent",
         "arguments": {"description": "test", "prompt": "do", "type": "plan_execute"}},
        {"id": "tc_6", "name": "user_select",
         "arguments": {"title": "pick", "options": ["a", "b"]}},
        {"id": "tc_7", "name": "ls", "arguments": {"path": "src/"}},
        {"id": "tc_8", "name": "update_file",
         "arguments": {"path": "c.txt", "old_string": "x", "new_string": "y"}},
    ]


# ═══════════════════════════════════════════════════════════════
# _READ_ONLY_TOOLS 常量
# ═══════════════════════════════════════════════════════════════

class TestReadOnlyTools:
    """_READ_ONLY_TOOLS 常量正确性"""

    def test_contains_read_file(self):
        assert "read_file" in _READ_ONLY_TOOLS

    def test_contains_search(self):
        assert "search" in _READ_ONLY_TOOLS

    def test_contains_find(self):
        assert "find" in _READ_ONLY_TOOLS

    def test_contains_ls(self):
        assert "ls" in _READ_ONLY_TOOLS

    def test_contains_web_search(self):
        assert "web_search" in _READ_ONLY_TOOLS

    def test_excludes_write_file(self):
        assert "write_file" not in _READ_ONLY_TOOLS

    def test_excludes_update_file(self):
        assert "update_file" not in _READ_ONLY_TOOLS

    def test_excludes_bash(self):
        assert "bash" not in _READ_ONLY_TOOLS

    def test_excludes_dispatch_agent(self):
        assert "dispatch_agent" not in _READ_ONLY_TOOLS

    def test_excludes_user_select(self):
        assert "user_select" not in _READ_ONLY_TOOLS

    def test_excludes_cp(self):
        assert "cp" not in _READ_ONLY_TOOLS

    def test_excludes_mv(self):
        assert "mv" not in _READ_ONLY_TOOLS

    def test_excludes_rm(self):
        assert "rm" not in _READ_ONLY_TOOLS

    def test_excludes_mk(self):
        assert "mk" not in _READ_ONLY_TOOLS

    def test_is_frozenset(self):
        assert isinstance(_READ_ONLY_TOOLS, frozenset)


# ═══════════════════════════════════════════════════════════════
# 四波分类与执行顺序
# ═══════════════════════════════════════════════════════════════

class TestWaveOrdering:
    """handle_tool_calls 四波执行顺序验证"""

    @pytest.mark.asyncio
    async def test_wave_order_user_select_first(self, chain, mock_agent, tool_calls_mixed):
        """Wave 0 (user_select) 最先执行"""
        wave_order = []

        async def record_wave(calls, **kwargs):
            names = [tc["name"] for tc in calls]
            wave_order.append(names)
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=record_wave)

        await chain.handle_tool_calls("content", tool_calls_mixed)

        # 第一次调用应为 user_select
        assert wave_order[0] == ["user_select"]

    @pytest.mark.asyncio
    async def test_wave_order_read_before_write(self, chain, mock_agent, tool_calls_mixed):
        """Wave 1 (只读) 在 Wave 2 (写) 之前执行"""
        wave_order = []

        async def record_wave(calls, **kwargs):
            names = [tc["name"] for tc in calls]
            wave_order.append(names)
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=record_wave)

        await chain.handle_tool_calls("content", tool_calls_mixed)

        # 第二个波次应全是只读工具
        assert set(wave_order[1]) == {"read_file", "search", "find", "ls"}

        # 第三个波次应全是写工具
        assert set(wave_order[2]) == {"write_file", "bash", "update_file"}

    @pytest.mark.asyncio
    async def test_dispatch_agent_last_wave(self, chain, mock_agent, tool_calls_mixed):
        """dispatch_agent 在所有其他工具之后执行"""
        wave_order = []

        async def record_wave(calls, **kwargs):
            names = [tc["name"] for tc in calls]
            wave_order.append(names)
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=record_wave)

        await chain.handle_tool_calls("content", tool_calls_mixed)

        # 最后一波应为 dispatch_agent
        assert wave_order[-1] == ["dispatch_agent"]

    @pytest.mark.asyncio
    async def test_read_only_parallel(self, chain, mock_agent):
        """Wave 1（只读工具）以 parallel=True 执行"""
        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "tc_1", "name": "search", "arguments": {"query": "x"}},
        ]
        parallel_flags = []

        async def record_parallel(calls, **kwargs):
            parallel_flags.append(kwargs.get("parallel"))
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=record_parallel)

        await chain.handle_tool_calls("content", calls)

        # Wave 1 应为 parallel=True（没有 user_select 所以 wave_order[0] 是只读）
        assert parallel_flags[0] is True

    @pytest.mark.asyncio
    async def test_write_serial(self, chain, mock_agent):
        """Wave 2（写工具）以 parallel=False 执行"""
        calls = [
            {"id": "tc_0", "name": "write_file",
             "arguments": {"path": "a.txt", "content": "x"}},
            {"id": "tc_1", "name": "bash",
             "arguments": {"command": "echo hi"}},
        ]
        parallel_flags = []

        async def record_parallel(calls, **kwargs):
            parallel_flags.append(kwargs.get("parallel"))
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=record_parallel)

        await chain.handle_tool_calls("content", calls)

        # Wave 2 应为 parallel=False
        assert parallel_flags[0] is False

    @pytest.mark.asyncio
    async def test_four_distinct_waves(self, chain, mock_agent, tool_calls_mixed):
        """验证正好产生四个波次"""
        wave_order = []

        async def record_wave(calls, **kwargs):
            names = [tc["name"] for tc in calls]
            wave_order.append(names)
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=record_wave)

        await chain.handle_tool_calls("content", tool_calls_mixed)

        assert len(wave_order) == 4
        assert wave_order[0] == ["user_select"]
        assert set(wave_order[1]) == {"read_file", "search", "find", "ls"}
        assert set(wave_order[2]) == {"write_file", "bash", "update_file"}
        assert wave_order[3] == ["dispatch_agent"]


# ═══════════════════════════════════════════════════════════════
# 结果顺序重建
# ═══════════════════════════════════════════════════════════════

class TestResultOrdering:
    """结果按原始 tool_calls 顺序重建"""

    @pytest.mark.asyncio
    async def test_results_preserve_original_order(self, chain, mock_agent, tool_calls_mixed):
        """执行完成后的 results 列表与原始 tool_calls 顺序一致"""
        async def execute_async(calls, **kwargs):
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=execute_async)

        await chain.handle_tool_calls("content", tool_calls_mixed)

        # 验证 _append_tool_result 按原始顺序调用
        appended_ids = [
            call_args[0][0] for call_args in
            mock_agent._append_tool_result.call_args_list
        ]
        expected_ids = [tc["id"] for tc in tool_calls_mixed]
        assert appended_ids == expected_ids

    @pytest.mark.asyncio
    async def test_results_missing_key_logs_warning(self, chain, mock_agent, caplog):
        """execute_async 未返回某工具的结果时，记录 warning 并生成降级结果"""
        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "tc_1", "name": "search", "arguments": {"query": "x"}},
        ]

        # Wave 1 只返回一个结果（丢失 tc_0）
        async def execute_async_missing(calls, **kwargs):
            if kwargs.get("parallel") is True:
                # 只返回 tc_1 的结果，丢失 tc_0
                return [("tc_1", "result_search", True)]
            return [(tc["id"], f"result_{tc['name']}", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(
            side_effect=execute_async_missing
        )

        import logging
        with caplog.at_level(logging.WARNING):
            await chain.handle_tool_calls("content", calls)

        # 应有 warning 日志
        assert any("工具结果丢失" in r.message for r in caplog.records)

        # 两个工具都应被 append
        assert mock_agent._append_tool_result.call_count == 2

        # tc_0 的降级结果应为失败
        first_call = mock_agent._append_tool_result.call_args_list[0]
        assert first_call[0][0] == "tc_0"
        assert first_call[0][1] == "错误：工具 'read_file' 结果丢失"


# ═══════════════════════════════════════════════════════════════
# dispatch_agent ParallelExecutor 集成
# ═══════════════════════════════════════════════════════════════

class TestDispatchAgentBarrier:
    """dispatch_agent 的 ParallelExecutor barrier 设置"""

    @pytest.mark.asyncio
    async def test_barrier_setup_when_dispatch_agent_present(self, chain, mock_agent):
        """有 dispatch_agent 时创建 ParallelExecutor，setup_barrier 被调用，
        且在 Wave 0-2 期间 _shared_executor 非 None"""
        executor_states = []  # 记录各阶段 _shared_executor 状态

        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "tc_1", "name": "dispatch_agent",
             "arguments": {"description": "a", "prompt": "p", "type": "plan_execute"}},
        ]

        async def execute_async_spy(calls, **kwargs):
            executor_states.append(mock_agent._shared_executor is not None)
            return [(tc["id"], "ok", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(
            side_effect=execute_async_spy
        )

        await chain.handle_tool_calls("content", calls)

        # Wave 1（read_file）期间 _shared_executor 应非 None
        assert executor_states[0] is True
        # Wave 3（dispatch_agent）期间 _shared_executor 应非 None
        assert executor_states[1] is True
        # finally 后 _shared_executor 应被清理
        assert mock_agent._shared_executor is None

    @pytest.mark.asyncio
    async def test_no_barrier_when_no_dispatch_agent(self, chain, mock_agent):
        """没有 dispatch_agent 时不创建 ParallelExecutor"""
        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]

        mock_agent._async_tool_executor.execute_async = AsyncMock(
            return_value=[("tc_0", "ok", True)]
        )

        await chain.handle_tool_calls("content", calls)

        # _shared_executor 应为 None（未被设置过）
        assert mock_agent._shared_executor is None

    @pytest.mark.asyncio
    async def test_barrier_cleaned_on_exception(self, chain, mock_agent):
        """异常时 finally 清理 _shared_executor 并释放 _all_done"""
        calls = [
            {"id": "tc_0", "name": "dispatch_agent",
             "arguments": {"description": "a", "prompt": "p", "type": "plan_execute"}},
            {"id": "tc_1", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]

        # read_file 在 Wave 1 执行时抛出异常
        async def execute_async_error(calls, **kwargs):
            raise RuntimeError("boom")

        mock_agent._async_tool_executor.execute_async = AsyncMock(
            side_effect=execute_async_error
        )

        with pytest.raises(RuntimeError, match="boom"):
            await chain.handle_tool_calls("content", calls)

        # 即使异常，finally 也应清理
        assert mock_agent._shared_executor is None

    @pytest.mark.asyncio
    async def test_all_done_released_on_early_failure(self, chain, mock_agent):
        """Wave 0-2 异常时 _all_done.set() 被调用，防止 dispatch_agent 协程永久挂起"""
        from unittest.mock import patch

        calls = [
            {"id": "tc_0", "name": "dispatch_agent",
             "arguments": {"description": "a", "prompt": "p", "type": "plan_execute"}},
            {"id": "tc_1", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]

        # 在 ParallelExecutor 构造后获取 spy 引用
        _all_done_spy = None

        orig_init = ToolCallbackChain.__init__

        async def execute_async_error(calls, **kwargs):
            # 在 Wave 1 执行时，_shared_executor 应已创建
            nonlocal _all_done_spy
            if mock_agent._shared_executor is not None:
                _all_done_spy = MagicMock(wraps=mock_agent._shared_executor._all_done)
                mock_agent._shared_executor._all_done = _all_done_spy
            raise RuntimeError("boom")

        mock_agent._async_tool_executor.execute_async = AsyncMock(
            side_effect=execute_async_error
        )

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
    async def test_on_before_delegates_to_method(self, chain, mock_agent):
        """_on_before / _on_after 正确委托到 _on_before_tool / _on_after_tool"""
        chain._on_before_tool = MagicMock()
        chain._on_after_tool = MagicMock()

        calls = [
            {"id": "tc_0", "name": "read_file", "arguments": {"path": "a.txt"}},
        ]
        usage = {"tool_parse_elapsed": 1.5}

        async def execute_async(calls, **kwargs):
            on_before = kwargs.get("on_before")
            on_after = kwargs.get("on_after")
            if on_before:
                on_before(calls[0], "detail_str")
            if on_after:
                on_after(calls[0], "output", True)
            return [(tc["id"], "ok", True) for tc in calls]

        mock_agent._async_tool_executor.execute_async = AsyncMock(side_effect=execute_async)

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
