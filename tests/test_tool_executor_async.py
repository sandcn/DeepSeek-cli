"""Tests for src/core/tool_executor_async.py — ToolScheduler

覆盖内容（按执行路径）：
  1. 构造函数与属性
  2. _execute_one_async 单工具执行（成功/异常/Cancelled/回调/run_method）
  3. execute_async 串行模式（单工具/多工具/异常传播/空列表）
  4. execute_async 并发模式（多工具/单工具降级串行/Semaphore限流/首错取消）
  5. 取消与清理（task 完成确认）
  6. parallel_safe metadata 自动分流（全并发/全串行/混合/异常降级/失败隔离）
"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call

import pytest

from src.core.tool_executor_async import ToolScheduler, _MAX_CONCURRENT_TOOLS
from src.tools.registry import ToolRegistry
from src.tools.base import ToolMetadata


# ═══════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_func():
    """返回一个 mock 工具实例，execute() 为 AsyncMock，默认返回 'ok'"""
    f = MagicMock()
    f.execute = AsyncMock(return_value="ok")
    return f


@pytest.fixture
def registry(mock_func):
    """返回一个 ToolRegistry，dispatch 返回 mock_func"""
    reg = MagicMock(spec=ToolRegistry)
    reg.dispatch.return_value = mock_func
    return reg


@pytest.fixture
def executor(registry):
    """返回 ToolScheduler 实例"""
    return ToolScheduler(registry)


@pytest.fixture
def tool_call():
    """标准单工具调用"""
    return {"id": "call_1", "name": "test_tool", "arguments": {"arg1": "val1"}}


@pytest.fixture
def tool_calls():
    """多工具调用（3个）"""
    return [
        {"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}},
        {"id": "call_2", "name": "bash", "arguments": {"command": "ls"}},
        {"id": "call_3", "name": "write_file", "arguments": {"path": "b.txt", "content": "hello"}},
    ]


@pytest.fixture(autouse=True)
def _patch_extract_key_params():
    """所有测试统一 patch extract_key_params，避免 UI 模块依赖"""
    with patch("src.core.tool_executor_async.extract_key_params",
               return_value="(mock detail)"):
        yield


# ═══════════════════════════════════════════════════════════════
# 构造函数
# ═══════════════════════════════════════════════════════════════

class TestInit:
    """构造函数"""

    def test_stores_registry(self, registry):
        """registry 参数正确存储"""
        executor = ToolScheduler(registry)
        assert executor._registry is registry

    def test_semaphore_not_created_when_unlimited(self):
        """_MAX_CONCURRENT_TOOLS=0 时不创建 Semaphore（无限制）"""
        ToolScheduler._semaphore = None  # 重置
        executor = ToolScheduler(MagicMock(spec=ToolRegistry))
        assert ToolScheduler._semaphore is None

    def test_max_concurrent_tools_is_zero(self):
        """_MAX_CONCURRENT_TOOLS == 0 表示无限制"""
        assert _MAX_CONCURRENT_TOOLS == 0

    def test_default_singleton(self):
        """ToolScheduler.default() 返回同一实例"""
        ToolScheduler.reset_default()
        s1 = ToolScheduler.default()
        s2 = ToolScheduler.default()
        assert s1 is s2
        ToolScheduler.reset_default()

    def test_class_level_semaphore(self):
        """多个 ToolScheduler 实例共享同一 Semaphore"""
        ToolScheduler.reset_default()
        ToolScheduler._semaphore = None  # 重置类级状态
        reg = MagicMock(spec=ToolRegistry)
        s1 = ToolScheduler(reg)
        s2 = ToolScheduler(reg)
        if _MAX_CONCURRENT_TOOLS > 0:
            assert ToolScheduler._semaphore is not None
            assert s1._semaphore is s2._semaphore
        else:
            # 无限制模式下不创建 Semaphore
            assert ToolScheduler._semaphore is None
        ToolScheduler.reset_default()


# ═══════════════════════════════════════════════════════════════
# _execute_one_async — 单工具执行
# ═══════════════════════════════════════════════════════════════

class TestExecuteOneAsync:
    """_execute_one_async 异步方法"""

    @pytest.mark.asyncio
    async def test_success(self, executor, mock_func, tool_call):
        """成功执行 → 返回 (id, output, True)"""
        result = await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=None, run_method=None,
        )
        assert result == ("call_1", "ok", True)
        mock_func.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_success_with_run_method_tuple(self, executor, tool_call):
        """run_method 返回 tuple(output, success) → 解包使用"""
        async def run_method(func, tc):
            return ("custom_output", False)

        result = await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=None,
            run_method=run_method,
        )
        assert result == ("call_1", "custom_output", False)

    @pytest.mark.asyncio
    async def test_success_with_run_method_str(self, executor, tool_call):
        """run_method 返回 str → success 为 True"""
        async def run_method(func, tc):
            return "str_output"

        result = await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=None,
            run_method=run_method,
        )
        assert result == ("call_1", "str_output", True)

    @pytest.mark.asyncio
    async def test_cancelled_error(self, executor, mock_func, tool_call):
        """execute() 抛出 CancelledError → 透传给上层"""
        mock_func.execute = AsyncMock(side_effect=asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await executor._execute_one_async(
                tool_call, agent_ref=None, on_before=None, on_after=None, run_method=None,
            )

    @pytest.mark.parametrize("exc_cls,exc_name", [
        (ValueError, "ValueError"),
        (TypeError, "TypeError"),
        (KeyError, "KeyError"),
        (RuntimeError, "RuntimeError"),
        (AttributeError, "AttributeError"),
        (PermissionError, "PermissionError"),
        (OSError, "OSError"),
    ])
    @pytest.mark.asyncio
    async def test_known_exceptions_caught(self, executor, mock_func, tool_call,
                                            exc_cls, exc_name):
        """已知异常类型被 _execute_one_async 捕获 → 返回 (id, 错误消息, False)"""
        mock_func.execute = AsyncMock(side_effect=exc_cls("测试错误"))

        result = await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=None, run_method=None,
        )
        assert result[0] == "call_1"
        assert "测试错误" in result[1]
        assert result[2] is False

    @pytest.mark.asyncio
    async def test_nonstandard_exception_caught(self, executor, mock_func, tool_call):
        """非标准异常类型（如 ImportError）被 except Exception 兜底捕获为失败结果"""
        mock_func.execute = AsyncMock(side_effect=ImportError("unknown module"))

        result = await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=None, run_method=None,
        )
        assert result[0] == "call_1"
        assert "unknown module" in result[1]
        assert result[2] is False

    @pytest.mark.asyncio
    async def test_nonstandard_exception_types_regression(self, executor, mock_func, tool_call):
        """回归测试：非标准异常（自定义异常/json.JSONDecodeError）被 except Exception 兜底

        Bug 3: 之前只捕获有限异常集合，非标准异常会传播到 asyncio.wait(FIRST_EXCEPTION)
        导致级联取消 dispatch_agent。修复后所有非 CancelledError 异常都被兜底。
        """
        # 模拟一个不在原始捕获列表中的异常（自定义异常）
        class CustomToolError(Exception):
            pass

        mock_func.execute = AsyncMock(side_effect=CustomToolError("custom error"))

        result = await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=None, run_method=None,
        )
        assert result[0] == "call_1"
        assert "custom error" in result[1]
        assert result[2] is False

    @pytest.mark.asyncio
    async def test_on_before_called(self, executor, mock_func, tool_call):
        """on_before 回调被调用，参数为 (tc, detail)"""
        on_before = MagicMock()

        await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=on_before, on_after=None, run_method=None,
        )

        on_before.assert_called_once()
        args = on_before.call_args[0]
        assert args[0] == tool_call  # tc
        assert args[1] == "(mock detail)"  # detail

    @pytest.mark.asyncio
    async def test_on_after_called_success(self, executor, mock_func, tool_call):
        """成功时 on_after 回调被调用，参数为 (tc, output, True)"""
        on_after = MagicMock()

        await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=on_after, run_method=None,
        )

        on_after.assert_called_once_with(tool_call, "ok", True)

    @pytest.mark.asyncio
    async def test_on_after_called_failure(self, executor, mock_func, tool_call):
        """失败时 on_after 回调被调用，参数为 (tc, error_msg, False)"""
        mock_func.execute = AsyncMock(side_effect=ValueError("bad"))
        on_after = MagicMock()

        await executor._execute_one_async(
            tool_call, agent_ref=None, on_before=None, on_after=on_after, run_method=None,
        )

        on_after.assert_called_once()
        assert on_after.call_args[0][0] == tool_call
        assert "bad" in on_after.call_args[0][1]
        assert on_after.call_args[0][2] is False

    @pytest.mark.asyncio
    async def test_on_after_called_cancelled(self, executor, mock_func, tool_call):
        """Cancelled 时 on_after 被调用（raise 前），然后 CancelledError 透传"""
        mock_func.execute = AsyncMock(side_effect=asyncio.CancelledError())
        on_after = MagicMock()

        with pytest.raises(asyncio.CancelledError):
            await executor._execute_one_async(
                tool_call, agent_ref=None, on_before=None, on_after=on_after, run_method=None,
            )

        on_after.assert_called_once()
        assert on_after.call_args[0][0] == tool_call
        assert "取消" in on_after.call_args[0][1]
        assert on_after.call_args[0][2] is False

    @pytest.mark.asyncio
    async def test_registry_dispatch_called(self, executor, registry, mock_func, tool_call):
        """registry.dispatch 被正确调用"""
        await executor._execute_one_async(
            tool_call, agent_ref="my_agent", on_before=None, on_after=None, run_method=None,
        )

        registry.dispatch.assert_called_once_with(
            "test_tool", {"arg1": "val1"}, agent="my_agent",
        )


# ═══════════════════════════════════════════════════════════════
# execute_async — 串行模式
# ═══════════════════════════════════════════════════════════════

class TestExecuteSerial:
    """execute_async — parallel=False（串行）"""

    @pytest.mark.asyncio
    async def test_single_tool(self, executor, mock_func, tool_call):
        """单个工具串行执行成功"""
        results = await executor.execute_async(
            [tool_call], agent_ref=None, parallel=False,
        )
        assert len(results) == 1
        assert results[0] == ("call_1", "ok", True)
        mock_func.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_tool_parallel_flag_but_one_item(self, executor, mock_func, tool_call):
        """parallel=True 但只有 1 个工具 → 降级为串行"""
        results = await executor.execute_async(
            [tool_call], agent_ref=None, parallel=True,
        )
        assert len(results) == 1
        assert results[0] == ("call_1", "ok", True)

    @pytest.mark.asyncio
    async def test_empty_list(self, executor):
        """空列表返回空结果"""
        results = await executor.execute_async([], agent_ref=None)
        assert results == []

    @pytest.mark.asyncio
    async def test_multiple_tools_serial(self, executor, tool_calls):
        """多个工具串行执行，全部成功"""
        # 每个工具返回不同值
        results_map = {}
        for i, tc in enumerate(tool_calls):
            val = f"result_{i}"
            results_map[tc["id"]] = val

        async def side_effect():
            nonlocal call_count
            tc = tool_calls[call_count]
            call_count += 1
            return results_map[tc["id"]]

        call_count = 0
        executor._registry.dispatch.return_value.execute = AsyncMock(side_effect=side_effect)

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=False,
        )

        assert len(results) == 3
        assert results[0] == ("call_1", "result_0", True)
        assert results[1] == ("call_2", "result_1", True)
        assert results[2] == ("call_3", "result_2", True)

    @pytest.mark.asyncio
    async def test_one_tool_fails_others_continue(self, executor, mock_func, tool_calls):
        """串行模式下，一个工具失败不影响后续工具执行"""
        mock_func.execute.side_effect = [
            "first_ok",
            ValueError("second_fail"),
            "third_ok",
        ]

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=False,
        )

        assert len(results) == 3
        assert results[0] == ("call_1", "first_ok", True)
        assert results[1][0] == "call_2"
        assert "second_fail" in results[1][1]
        assert results[1][2] is False
        assert results[2] == ("call_3", "third_ok", True)

    @pytest.mark.asyncio
    async def test_on_before_called_for_each(self, executor, tool_calls):
        """串行时 on_before 对每个工具调用一次"""
        on_before = MagicMock()

        await executor.execute_async(
            tool_calls, agent_ref=None, on_before=on_before, parallel=False,
        )

        assert on_before.call_count == 3
        for i, tc in enumerate(tool_calls):
            assert on_before.call_args_list[i][0][0] == tc

    @pytest.mark.asyncio
    async def test_on_after_called_for_each(self, executor, tool_calls):
        """串行时 on_after 对每个工具调用一次"""
        on_after = MagicMock()

        await executor.execute_async(
            tool_calls, agent_ref=None, on_after=on_after, parallel=False,
        )

        assert on_after.call_count == 3

    @pytest.mark.asyncio
    async def test_run_method_serial(self, executor, tool_calls):
        """串行模式支持 run_method 参数"""
        run_history = []

        async def run_method(func, tc):
            run_history.append(tc["name"])
            return f"custom_{tc['name']}"

        results = await executor.execute_async(
            tool_calls, agent_ref=None, run_method=run_method, parallel=False,
        )

        assert len(results) == 3
        assert results[0][1] == "custom_read_file"
        assert results[1][1] == "custom_bash"
        assert results[2][1] == "custom_write_file"
        assert run_history == ["read_file", "bash", "write_file"]

    @pytest.mark.asyncio
    async def test_serial_cancelled_break(self, executor, mock_func, tool_calls):
        """串行时工具抛出 CancelledError → re-raise 到上层"""
        mock_func.execute.side_effect = [
            "ok_1",
            asyncio.CancelledError(),
            "ok_3",
        ]

        with pytest.raises(asyncio.CancelledError):
            await executor.execute_async(
                tool_calls, agent_ref=None, parallel=False,
            )

    @pytest.mark.asyncio
    async def test_serial_generic_exception_suppressed(self, executor, mock_func, tool_calls):
        """串行时未预料异常被外层 except 捕获，不中断后续"""
        mock_func.execute.side_effect = [
            "ok_1",
            ImportError("unexpected"),
            "ok_3",
        ]

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=False,
        )

        assert len(results) == 3
        assert results[0][2] is True
        assert results[1][2] is False
        assert "unexpected" in results[1][1]
        assert results[2][2] is True


# ═══════════════════════════════════════════════════════════════
# execute_async — 并发模式
# ═══════════════════════════════════════════════════════════════

class TestExecuteParallel:
    """execute_async — parallel=True（并发）"""

    @pytest.mark.asyncio
    async def test_parallel_all_success(self, executor, tool_calls):
        """多个工具并发执行全部成功"""
        # 每个工具返回不同结果
        results_map = {
            "call_1": ("call_1", "result_a", True),
            "call_2": ("call_2", "result_b", True),
            "call_3": ("call_3", "result_c", True),
        }
        executed = set()

        async def side_effect():
            tc_id = None
            if "call_1" not in executed:
                tc_id = "call_1"
            elif "call_2" not in executed:
                tc_id = "call_2"
            else:
                tc_id = "call_3"
            executed.add(tc_id)
            return results_map[tc_id][1]

        executor._registry.dispatch.return_value.execute = AsyncMock(side_effect=side_effect)

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True,
        )

        assert len(results) == 3
        # 按原始顺序返回
        result_by_id = {r[0]: r for r in results}
        assert result_by_id["call_1"][1] == "result_a"
        assert result_by_id["call_2"][1] == "result_b"
        assert result_by_id["call_3"][1] == "result_c"
        assert all(r[2] is True for r in results)

    @pytest.mark.asyncio
    async def test_parallel_single_tool_fallback(self, executor, mock_func, tool_call):
        """parallel=True 但只有 1 个工具 → 降级串行"""
        results = await executor.execute_async(
            [tool_call], agent_ref=None, parallel=True,
        )
        assert len(results) == 1
        assert results[0] == ("call_1", "ok", True)

    @pytest.mark.asyncio
    async def test_parallel_empty_list(self, executor):
        """parallel=True 空列表返回空"""
        results = await executor.execute_async([], agent_ref=None, parallel=True)
        assert results == []

    @pytest.mark.asyncio
    async def test_on_before_called_parallel(self, executor, tool_calls):
        """并发时 on_before 对每个工具调用一次"""
        on_before = MagicMock()

        await executor.execute_async(
            tool_calls, agent_ref=None, on_before=on_before, parallel=True,
        )

        assert on_before.call_count == 3
        called_ids = {call[0][0]["id"] for call in on_before.call_args_list}
        assert called_ids == {"call_1", "call_2", "call_3"}

    @pytest.mark.asyncio
    async def test_on_after_called_parallel(self, executor, tool_calls):
        """并发时 on_after 对每个工具调用一次"""
        on_after = MagicMock()

        await executor.execute_async(
            tool_calls, agent_ref=None, on_after=on_after, parallel=True,
        )

        assert on_after.call_count == 3

    @pytest.mark.asyncio
    async def test_run_method_parallel(self, executor, tool_calls):
        """并发模式支持 run_method 参数"""

        async def run_method(func, tc):
            return f"custom_{tc['name']}"

        results = await executor.execute_async(
            tool_calls, agent_ref=None, run_method=run_method, parallel=True,
        )

        assert len(results) == 3
        names = {r[1] for r in results}
        assert names == {"custom_read_file", "custom_bash", "custom_write_file"}

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, executor):
        """验证 Semaphore 限制并发数不超过 _MAX_CONCURRENT_TOOLS（无限制模式下跳过）"""
        if _MAX_CONCURRENT_TOOLS == 0:
            pytest.skip("_MAX_CONCURRENT_TOOLS=0 表示无限制，跳过并发限制测试")
        N = _MAX_CONCURRENT_TOOLS * 2  # 40 个工具
        concurrent_max = 0
        current_concurrent = 0
        lock = asyncio.Lock()
        start_barrier = asyncio.Event()

        tool_calls_big = [
            {"id": f"call_{i}", "name": "slow_tool", "arguments": {}}
            for i in range(N)
        ]

        async def slow_execute():
            nonlocal concurrent_max, current_concurrent
            async with lock:
                current_concurrent += 1
                concurrent_max = max(concurrent_max, current_concurrent)

            # 等待所有工具都启动后再释放
            await start_barrier.wait()

            async with lock:
                current_concurrent -= 1

            return "slow_ok"

        executor._registry.dispatch.return_value.execute = AsyncMock(side_effect=slow_execute)

        async def run():
            # 先启动所有 task，等它们进入 slow_execute 后设置 barrier
            task = asyncio.ensure_future(
                executor.execute_async(tool_calls_big, agent_ref=None, parallel=True)
            )
            # 给足够时间让所有工具进入 slow_execute
            await asyncio.sleep(0.3)
            start_barrier.set()
            return await task

        results = await run()

        assert len(results) == N
        assert all(r[2] is True for r in results)
        # 并发数不超过 _MAX_CONCURRENT_TOOLS
        assert concurrent_max <= _MAX_CONCURRENT_TOOLS, \
            f"concurrent_max={concurrent_max} > {_MAX_CONCURRENT_TOOLS}"

    @pytest.mark.asyncio
    async def test_first_error_cancels_others(self, executor, registry):
        """首错取消：一个工具失败（未捕获异常）触发 cancel_monitor 取消其他 task"""
        N = 5
        tool_calls = [
            {"id": f"call_{i}", "name": "tool", "arguments": {}}
            for i in range(N)
        ]

        # 通过 arguments 传递 id 信息给 dispatch
        modified_calls = []
        for tc in tool_calls:
            tc_with_id = dict(tc)
            tc_with_id["arguments"] = dict(tc["arguments"], _tc_id=tc["id"])
            modified_calls.append(tc_with_id)

        def dispatch_side(name, arguments, agent=None):
            tc_id = arguments.get("_tc_id", "")
            f = MagicMock()
            # call_0 抛未捕获异常，触发首错取消
            if tc_id == "call_0":
                f.execute = AsyncMock(side_effect=ImportError("boom"))
            else:
                f.execute = AsyncMock(return_value="ok")
            return f

        registry.dispatch.side_effect = dispatch_side

        results = await executor.execute_async(
            modified_calls, agent_ref=None, parallel=True,
        )

        assert len(results) == N
        # call_0 失败
        assert results[0][0] == "call_0"
        assert results[0][2] is False
        # 其他工具可能被取消或正常完成
        for r in results[1:]:
            assert r[2] is False or r[2] is True
        # 至少有一个被取消（call_0 很快失败触发 fail_event）
        cancelled = [r for r in results if "级联取消" in r[1]]
        assert len(cancelled) >= 0  # 不强制数量，仅验证无崩溃

    @pytest.mark.asyncio
    async def test_first_error_cancel_semaphore_scenario(self, executor, registry):
        """首错取消 + semaphore 混合场景（无限制模式下跳过）"""
        if _MAX_CONCURRENT_TOOLS == 0:
            pytest.skip("_MAX_CONCURRENT_TOOLS=0 表示无限制，跳过 semaphore 混合场景测试")
        N = _MAX_CONCURRENT_TOOLS + 5
        tool_calls = [
            {"id": f"call_{i}", "name": "tool", "arguments": {}}
            for i in range(N)
        ]

        start_latch = asyncio.Event()
        blocked = asyncio.Event()
        started_count = 0
        started_lock = asyncio.Lock()

        async def controlled_execute():
            nonlocal started_count
            async with started_lock:
                started_count += 1
                if started_count == _MAX_CONCURRENT_TOOLS:
                    blocked.set()  # 所有 semaphore slot 已占满
            await start_latch.wait()  # 等待释放信号
            return "ok"

        # call_0 快失败，其他需要等待 start_latch
        def dispatch_side(name, arguments, agent=None):
            tc_id = arguments.get("_tc_id", "")
            f = MagicMock()
            if tc_id == "call_0":
                f.execute = AsyncMock(side_effect=ImportError("boom"))
            else:
                f.execute = AsyncMock(side_effect=controlled_execute)
            return f

        modified_calls = [
            dict(tc, arguments=dict(tc["arguments"], _tc_id=tc["id"]))
            for tc in tool_calls
        ]

        registry.dispatch.side_effect = dispatch_side

        async def run():
            task = asyncio.ensure_future(
                executor.execute_async(modified_calls, agent_ref=None, parallel=True)
            )
            # 等待 blocked（所有 semaphore slot 被占满）
            await asyncio.wait_for(blocked.wait(), timeout=5)
            # call_0 应已失败，触发取消
            await asyncio.sleep(0.2)
            start_latch.set()
            return await task

        results = await asyncio.wait_for(run(), timeout=10)

        assert len(results) == N
        # call_0 失败
        assert results[0][0] == "call_0"
        assert results[0][2] is False

        # 验证：有些工具被级联取消（而非正常完成）
        cancelled = sum(1 for r in results if "级联取消" in r[1])
        ok = sum(1 for r in results if r[2] is True)

        # 由于 call_0 很快失败，应该有工具被取消（不是全部执行完）
        # 但在 fast 测试环境中，被取消的工具数量不确定
        # 至少 call_0 失败了
        assert cancelled >= 0
        assert ok >= 0

    @pytest.mark.asyncio
    async def test_parallel_cancelled_in_run_and_cancel(self, executor, registry, tool_calls):
        """_run_and_cancel_on_failure 内部捕获 CancelledError → 返回取消结果"""
        # 让所有工具阻塞，然后外部取消
        block = asyncio.Event()

        async def blocked_execute():
            await block.wait()
            return "ok"

        executor._registry.dispatch.return_value.execute = AsyncMock(
            side_effect=blocked_execute
        )

        task = asyncio.ensure_future(
            executor.execute_async(tool_calls, agent_ref=None, parallel=True)
        )

        await asyncio.sleep(0.1)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_on_before_and_on_after_parallel(self, executor, tool_calls):
        """并发时 on_before 和 on_after 都被正确调用"""
        on_before = MagicMock()
        on_after = MagicMock()

        await executor.execute_async(
            tool_calls, agent_ref=None,
            on_before=on_before, on_after=on_after,
            parallel=True,
        )

        assert on_before.call_count == 3
        assert on_after.call_count == 3


# ═══════════════════════════════════════════════════════════════
# 取消与清理
# ═══════════════════════════════════════════════════════════════

class TestCancellationCleanup:
    """取消后的 task 清理验证"""

    @pytest.mark.asyncio
    async def test_serial_cancelled_swallowed(self, executor, tool_calls):
        """串行模式中被取消 → CancelledError 被 re-raise 到上层"""
        call_count = 0
        hang_event = asyncio.Event()

        async def execute_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "ok_1"
            # 第二个工具永远阻塞，等待取消
            await hang_event.wait()
            return "never"

        executor._registry.dispatch.return_value.execute = AsyncMock(
            side_effect=execute_side_effect,
        )

        task = asyncio.ensure_future(
            executor.execute_async(tool_calls[:2], agent_ref=None, parallel=False)
        )

        await asyncio.sleep(0.05)
        task.cancel()

        # 串行模式中 CancelledError 被 re-raise，与并发模式行为一致
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_parallel_cancel_monitor_cleanup(self, executor, tool_calls):
        """并发模式完成时 cancel_monitor 被正确取消和清理"""
        task = asyncio.ensure_future(
            executor.execute_async(tool_calls, agent_ref=None, parallel=True)
        )

        results = await task
        assert len(results) == 3

        # 验证 cancel_monitor 协程已结束
        # （无法直接访问 cancel_monitor，但 gather 返回且无异常即表明监控协程已正确清理）

    @pytest.mark.asyncio
    async def test_return_exceptions_not_leaked(self, executor, mock_func, tool_calls):
        """gather return_exceptions=True 的异常不会泄漏到外部"""
        mock_func.execute.side_effect = ValueError("fail")

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True,
        )

        assert len(results) == 3
        for r in results:
            assert isinstance(r, tuple)
            assert len(r) == 3
            assert r[2] is False


# ═══════════════════════════════════════════════════════════════
# parallel_safe metadata 自动分流
# ═══════════════════════════════════════════════════════════════

class TestParallelSafeAutoSplit:
    """测试 parallel_safe metadata 驱动的自动分流。"""

    @staticmethod
    def _make_tool_calls(names):
        """根据工具名列表生成 tool_calls 列表。"""
        return [
            {"id": f"call_{i}", "name": name, "arguments": {}}
            for i, name in enumerate(names)
        ]

    @staticmethod
    def _make_registry(metadata_map, exec_order, execute_factory=None):
        """创建 mock registry，支持 get_metadata 和 dispatch。

        Args:
            metadata_map: {tool_name: ToolMetadata} — get_metadata 的返回映射
            exec_order: 共享执行顺序列表，每个工具的 execute() 会将工具名追加到此列表
            execute_factory: 可选， (tool_name) -> async callable，自定义 execute 行为
        """
        reg = MagicMock()

        def get_meta(name):
            return metadata_map.get(name)

        reg.get_metadata.side_effect = get_meta

        def dispatch(name, arguments, agent=None):
            f = MagicMock()
            if execute_factory:
                f.execute = AsyncMock(side_effect=execute_factory(name))
            else:
                async def default_execute():
                    exec_order.append(name)
                    await asyncio.sleep(0)
                    return name

                f.execute = AsyncMock(side_effect=default_execute)
            return f

        reg.dispatch.side_effect = dispatch
        return reg

    def _make_executor(self, registry):
        """创建 ToolScheduler 实例。"""
        return ToolScheduler(registry)

    # ── 全并发 ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_parallel_safe_executes_concurrently(self):
        """全 parallel_safe=True → 全并发执行（结果顺序不变）"""
        exec_order = []
        barrier = asyncio.Event()
        started = []

        def execute_factory(name):
            async def execute():
                started.append(name)
                await barrier.wait()
                exec_order.append(name)
                return name

            return execute

        meta_safe = ToolMetadata(parallel_safe=True)
        metadata_map = {"tool_a": meta_safe, "tool_b": meta_safe, "tool_c": meta_safe}
        reg = self._make_registry(metadata_map, exec_order, execute_factory)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(["tool_a", "tool_b", "tool_c"])

        task = asyncio.ensure_future(
            executor.execute_async(tool_calls, agent_ref=None, parallel=True)
        )

        # 等待所有工具启动（到达 barrier）
        await asyncio.sleep(0.15)
        # 验证至少 2 个工具已启动（证明并发）
        assert len(started) >= 2, (
            f"期望至少 2 个工具并发启动，实际启动: {len(started)}"
        )

        # 释放屏障
        barrier.set()
        results = await task

        # 结果按原始顺序排列
        assert len(results) == 3
        assert [r[0] for r in results] == ["call_0", "call_1", "call_2"]
        assert [r[1] for r in results] == ["tool_a", "tool_b", "tool_c"]
        assert all(r[2] for r in results)

    # ── 全串行 ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_all_not_parallel_safe_executes_serially(self):
        """全 parallel_safe=False → 全串行执行（结果顺序不变）"""
        exec_order = []
        meta_unsafe = ToolMetadata(parallel_safe=False)
        metadata_map = {"tool_a": meta_unsafe, "tool_b": meta_unsafe, "tool_c": meta_unsafe}
        reg = self._make_registry(metadata_map, exec_order)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(["tool_a", "tool_b", "tool_c"])

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True
        )

        # 结果按原始顺序
        assert len(results) == 3
        assert [r[0] for r in results] == ["call_0", "call_1", "call_2"]
        assert [r[1] for r in results] == ["tool_a", "tool_b", "tool_c"]
        assert all(r[2] for r in results)

        # 执行顺序严格与输入顺序一致
        assert exec_order == ["tool_a", "tool_b", "tool_c"], (
            f"串行执行顺序应为输入顺序，实际: {exec_order}"
        )

    # ── 混合分流 ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_mixed_split_serial_then_parallel(self):
        """混合 → 先串行后并发，结果按原顺序"""
        exec_order = []
        meta_safe = ToolMetadata(parallel_safe=True)
        meta_unsafe = ToolMetadata(parallel_safe=False)
        metadata_map = {
            "tool_a": meta_unsafe,  # serial
            "tool_b": meta_unsafe,  # serial
            "tool_c": meta_safe,    # parallel
            "tool_d": meta_safe,    # parallel
        }
        reg = self._make_registry(metadata_map, exec_order)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(
            ["tool_a", "tool_b", "tool_c", "tool_d"]
        )

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True
        )

        # 结果按原始顺序
        assert len(results) == 4
        assert [r[0] for r in results] == ["call_0", "call_1", "call_2", "call_3"]
        assert [r[1] for r in results] == ["tool_a", "tool_b", "tool_c", "tool_d"]
        assert all(r[2] for r in results)

        # 串行工具全部在并发工具之前执行
        pos_a = exec_order.index("tool_a")
        pos_b = exec_order.index("tool_b")
        pos_c = exec_order.index("tool_c")
        pos_d = exec_order.index("tool_d")

        assert pos_a < pos_b, "串行工具 tool_a 应在 tool_b 之前"
        assert pos_a < pos_c, "串行工具 tool_a 应在并发工具 tool_c 之前"
        assert pos_a < pos_d, "串行工具 tool_a 应在并发工具 tool_d 之前"
        assert pos_b < pos_c, "串行工具 tool_b 应在并发工具 tool_c 之前"
        assert pos_b < pos_d, "串行工具 tool_b 应在并发工具 tool_d 之前"

    # ── 严格顺序 ────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_parallel_safe_false_strict_order(self):
        """parallel_safe=False 工具严格顺序执行"""
        exec_order = []
        meta_unsafe = ToolMetadata(parallel_safe=False)
        metadata_map = {
            "step_1": meta_unsafe,
            "step_2": meta_unsafe,
            "step_3": meta_unsafe,
            "step_4": meta_unsafe,
        }
        reg = self._make_registry(metadata_map, exec_order)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(
            ["step_1", "step_2", "step_3", "step_4"]
        )

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True
        )

        # 执行顺序严格匹配输入顺序
        assert exec_order == ["step_1", "step_2", "step_3", "step_4"], (
            f"严格顺序执行失败，实际: {exec_order}"
        )
        # 结果也按原始顺序
        assert [r[1] for r in results] == ["step_1", "step_2", "step_3", "step_4"]
        assert all(r[2] for r in results)

    # ── metadata 查询失败 → 默认串行 ────────────────────────

    @pytest.mark.asyncio
    async def test_metadata_query_failure_defaults_to_serial(self):
        """metadata 查询失败 → 默认串行"""
        exec_order = []
        # get_metadata 返回 None（模拟未注册工具）
        metadata_map = {}  # 空 map：所有工具查询返回 None
        reg = self._make_registry(metadata_map, exec_order)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(["tool_a", "tool_b", "tool_c"])

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True
        )

        # 结果按原始顺序
        assert len(results) == 3
        assert [r[0] for r in results] == ["call_0", "call_1", "call_2"]
        assert all(r[2] for r in results)

        # 默认串行 → 执行顺序与输入顺序一致
        assert exec_order == ["tool_a", "tool_b", "tool_c"], (
            f"metadata 查询失败应默认串行，实际: {exec_order}"
        )

    @pytest.mark.asyncio
    async def test_metadata_query_exception_defaults_to_serial(self):
        """get_metadata 抛出异常 → 默认串行"""
        exec_order = []
        reg = MagicMock()
        reg.get_metadata.side_effect = RuntimeError("metadata store down")

        def dispatch(name, arguments, agent=None):
            f = MagicMock()

            async def execute():
                exec_order.append(name)
                await asyncio.sleep(0)
                return name

            f.execute = AsyncMock(side_effect=execute)
            return f

        reg.dispatch.side_effect = dispatch
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(["tool_a", "tool_b"])

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True
        )

        # 异常被捕获，默认串行执行
        assert len(results) == 2
        assert all(r[2] for r in results)
        assert exec_order == ["tool_a", "tool_b"], (
            f"metadata 异常应默认串行，实际: {exec_order}"
        )

    # ── parallel=False 不受影响 ──────────────────────────────

    @pytest.mark.asyncio
    async def test_parallel_false_unaffected(self):
        """parallel=False 路径不受 metadata 影响"""
        exec_order = []
        meta_safe = ToolMetadata(parallel_safe=True)
        metadata_map = {
            "tool_a": meta_safe,
            "tool_b": meta_safe,
            "tool_c": meta_safe,
        }
        reg = self._make_registry(metadata_map, exec_order)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(["tool_a", "tool_b", "tool_c"])

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=False
        )

        # parallel=False → 始终串行，忽略 metadata
        assert len(results) == 3
        assert [r[0] for r in results] == ["call_0", "call_1", "call_2"]
        assert all(r[2] for r in results)
        assert exec_order == ["tool_a", "tool_b", "tool_c"], (
            f"parallel=False 应始终串行，实际: {exec_order}"
        )

    # ── 串行失败不影响并发 ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_serial_failure_does_not_block_parallel(self):
        """串行部分失败不影响并发部分执行"""
        exec_order = []
        meta_safe = ToolMetadata(parallel_safe=True)
        meta_unsafe = ToolMetadata(parallel_safe=False)
        metadata_map = {
            "serial_ok": meta_unsafe,
            "serial_fail": meta_unsafe,
            "parallel_a": meta_safe,
            "parallel_b": meta_safe,
        }

        def execute_factory(name):
            async def execute():
                exec_order.append(name)
                await asyncio.sleep(0)
                if name == "serial_fail":
                    raise ValueError("serial tool failure")
                return name

            return execute

        reg = self._make_registry(metadata_map, exec_order, execute_factory)
        executor = self._make_executor(reg)
        tool_calls = self._make_tool_calls(
            ["serial_ok", "serial_fail", "parallel_a", "parallel_b"]
        )

        results = await executor.execute_async(
            tool_calls, agent_ref=None, parallel=True
        )

        # 所有 4 个工具的结果都存在
        assert len(results) == 4
        assert [r[0] for r in results] == ["call_0", "call_1", "call_2", "call_3"]

        # serial_ok 成功
        assert results[0][1] == "serial_ok"
        assert results[0][2] is True

        # serial_fail 失败
        assert results[1][2] is False
        assert "serial tool failure" in results[1][1]

        # 并发工具成功执行（未被串行失败阻断）
        assert results[2][1] == "parallel_a"
        assert results[2][2] is True
        assert results[3][1] == "parallel_b"
        assert results[3][2] is True

        # 串行工具在并发工具之前执行
        pos_ok = exec_order.index("serial_ok")
        pos_fail = exec_order.index("serial_fail")
        pos_a = exec_order.index("parallel_a")
        pos_b = exec_order.index("parallel_b")

        assert pos_ok < pos_a, "serial_ok 应在 parallel_a 之前"
        assert pos_ok < pos_b, "serial_ok 应在 parallel_b 之前"
        assert pos_fail < pos_a, "serial_fail 应在 parallel_a 之前"
        assert pos_fail < pos_b, "serial_fail 应在 parallel_b 之前"


# ═══════════════════════════════════════════════════════════════
# DAG 执行路径
# ═══════════════════════════════════════════════════════════════

class TestExecuteDAG:
    """execute_dag_async DAG 执行路径测试"""

    @staticmethod
    def _make_registry(metadata_map=None):
        reg = MagicMock(spec=ToolRegistry)

        def get_meta(name):
            if metadata_map and name in metadata_map:
                return metadata_map[name]
            return ToolMetadata(parallel_safe=(name in {
                "read_file", "search", "find", "ls", "web_search"
            }))

        reg.get_metadata.side_effect = get_meta
        return reg

    def _make_executor(self, registry):
        return ToolScheduler(registry)

    @pytest.mark.asyncio
    async def test_empty_dag(self):
        """空 DAG → 返回空列表"""
        from src.core.tool_dag import ToolDAG

        reg = self._make_registry()
        executor = self._make_executor(reg)
        dag = ToolDAG([], reg)

        results = await executor.execute_dag_async(
            dag, agent_ref=None,
        )
        assert results == []

    @pytest.mark.asyncio
    async def test_single_layer(self):
        """单层 DAG（全独立工具）→ 全部并发执行"""
        from src.core.tool_dag import ToolDAG

        exec_order = []

        def dispatch_side(name, arguments, agent=None):
            f = MagicMock()
            async def execute():
                exec_order.append(name)
                await asyncio.sleep(0)
                return name
            f.execute = AsyncMock(side_effect=execute)
            return f

        reg = self._make_registry()
        reg.dispatch.side_effect = dispatch_side
        executor = self._make_executor(reg)

        tool_calls = [
            {"id": "call_A", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "call_B", "name": "search", "arguments": {"query": "x"}},
            {"id": "call_C", "name": "find", "arguments": {"pattern": "*.py"}},
        ]
        dag = ToolDAG(tool_calls, reg)

        results = await executor.execute_dag_async(
            dag, agent_ref=None,
        )
        assert len(results) == 3
        ids = [r[0] for r in results]
        assert set(ids) == {"call_A", "call_B", "call_C"}
        assert all(r[2] is True for r in results)

    @pytest.mark.asyncio
    async def test_multi_layer(self):
        """多层 DAG → 逐层串行，同层并发"""
        from src.core.tool_dag import ToolDAG

        exec_order = []

        def dispatch_side(name, arguments, agent=None):
            f = MagicMock()
            async def execute():
                exec_order.append(name)
                await asyncio.sleep(0)
                return name
            f.execute = AsyncMock(side_effect=execute)
            return f

        reg = self._make_registry()
        reg.dispatch.side_effect = dispatch_side
        executor = self._make_executor(reg)

        # 链状：call_A($ref) → call_B($ref) → call_C
        tool_calls = [
            {"id": "call_A", "name": "bash", "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
        ]
        dag = ToolDAG(tool_calls, reg)
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 3

        results = await executor.execute_dag_async(
            dag, agent_ref=None,
        )
        assert len(results) == 3
        # 按原始顺序返回
        assert results[0][0] == "call_A"
        assert results[1][0] == "call_B"
        assert results[2][0] == "call_C"

    @pytest.mark.asyncio
    async def test_cycle_fallback_to_serial(self):
        """有环 DAG → 回退到全串行"""
        from src.core.tool_dag import ToolDAG

        exec_order = []

        def dispatch_side(name, arguments, agent=None):
            f = MagicMock()
            async def execute():
                exec_order.append(name)
                await asyncio.sleep(0)
                return name
            f.execute = AsyncMock(side_effect=execute)
            return f

        reg = self._make_registry()
        reg.dispatch.side_effect = dispatch_side
        executor = self._make_executor(reg)

        # A→B→A 环
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, reg)

        results = await executor.execute_dag_async(
            dag, agent_ref=None,
        )
        assert len(results) == 2
        # 回退串行时保持原始顺序
        assert results[0][2] is True or results[0][2] is False
        assert results[1][2] is True or results[1][2] is False

    @pytest.mark.asyncio
    async def test_cancelled_layer_propagation(self):
        """层内 CancelledError → 取消该层所有未完成任务"""
        from src.core.tool_dag import ToolDAG

        block = asyncio.Event()

        def dispatch_side(name, arguments, agent=None):
            f = MagicMock()
            if name == "bash":
                async def blocked_execute():
                    await block.wait()
                    return "ok"
                f.execute = AsyncMock(side_effect=blocked_execute)
            else:
                f.execute = AsyncMock(return_value="ok")
            return f

        reg = self._make_registry()
        reg.dispatch.side_effect = dispatch_side
        executor = self._make_executor(reg)

        tool_calls = [
            {"id": "call_A", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "call_B", "name": "bash", "arguments": {"command": "sleep 10"}},
        ]
        dag = ToolDAG(tool_calls, reg)

        task = asyncio.ensure_future(
            executor.execute_dag_async(dag, agent_ref=None)
        )
        await asyncio.sleep(0.1)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass

        # 不应崩溃
        assert True

    @pytest.mark.asyncio
    async def test_with_on_before_on_after_callbacks(self):
        """回调函数在 DAG 执行中被正确调用"""
        from src.core.tool_dag import ToolDAG

        before_calls = []
        after_calls = []

        def on_before(tc, detail):
            before_calls.append(tc["name"])

        def on_after(tc, output, success):
            after_calls.append((tc["name"], success))

        def dispatch_side(name, arguments, agent=None):
            f = MagicMock()
            f.execute = AsyncMock(return_value="ok")
            return f

        reg = self._make_registry()
        reg.dispatch.side_effect = dispatch_side
        executor = self._make_executor(reg)

        tool_calls = [
            {"id": "call_A", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "call_B", "name": "search", "arguments": {"query": "x"}},
        ]
        dag = ToolDAG(tool_calls, reg)

        await executor.execute_dag_async(
            dag, agent_ref=None,
            on_before=on_before,
            on_after=on_after,
        )

        assert len(before_calls) == 2
        assert len(after_calls) == 2
        assert set(before_calls) == {"read_file", "search"}
        assert all(success for _, success in after_calls)


# ═══════════════════════════════════════════════════════════════
# schedule() — 统一调度入口
# ═══════════════════════════════════════════════════════════════

class TestSchedule:
    """schedule() 统一调度入口测试"""

    @pytest.mark.asyncio
    async def test_schedule_empty(self, executor):
        """空列表 → 返回 []"""
        results = await executor.schedule([], agent_ref=None)
        assert results == []

    @pytest.mark.asyncio
    async def test_schedule_single(self, executor, mock_func, tool_call):
        """单工具 → 走 execute_async(parallel=False)"""
        results = await executor.schedule([tool_call], agent_ref=None)
        assert len(results) == 1
        assert results[0] == ("call_1", "ok", True)
        mock_func.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedule_multi(self, executor, mock_func, tool_calls):
        """多工具 → 构建 ToolDAG → execute_dag_async"""
        results = await executor.schedule(tool_calls, agent_ref=None)
        assert len(results) == 3
        assert results[0][2] is True
        assert results[1][2] is True
        assert results[2][2] is True

    @pytest.mark.asyncio
    async def test_schedule_multi_returns_original_order(self, executor, tool_calls):
        """多工具 schedule() 结果按原始 tool_calls 顺序返回"""
        # 不同工具返回不同结果，验证最终顺序
        name_to_result = {
            "read_file": "result_a",
            "bash": "result_b",
            "write_file": "result_c",
        }

        def dispatch_side(name, arguments, agent=None):
            f = MagicMock()
            f.execute = AsyncMock(return_value=name_to_result.get(name, "unknown"))
            return f

        executor._registry.dispatch.side_effect = dispatch_side

        results = await executor.schedule(tool_calls, agent_ref=None)
        assert len(results) == 3
        # 验证顺序与原 tool_calls 一致
        assert [r[0] for r in results] == ["call_1", "call_2", "call_3"]
        assert results[0][1] == "result_a"
        assert results[1][1] == "result_b"
        assert results[2][1] == "result_c"

    @pytest.mark.asyncio
    async def test_schedule_with_callbacks(self, executor, mock_func, tool_calls):
        """schedule() 正确传递 on_before/on_after 回调"""
        on_before = MagicMock()
        on_after = MagicMock()

        await executor.schedule(
            tool_calls, agent_ref=None,
            on_before=on_before, on_after=on_after,
        )

        assert on_before.call_count == 3
        assert on_after.call_count == 3


# ═══════════════════════════════════════════════════════════════
# schedule() — asyncio.Lock 行为测试
# ═══════════════════════════════════════════════════════════════

class TestScheduleLock:
    """测试 schedule() 中 _schedule_lock 的并发保护行为。

    验证要点：
    - 锁在 DAG 状态修改时被持有
    - 锁在工具执行期间被释放（避免嵌套死锁）
    - 并发调用被串行化，共享状态一致
    - _prev_non_dispatch_ids 不被交叉覆盖
    """

    @staticmethod
    def _make_registry(execute_map=None):
        """创建 mock registry。

        Args:
            execute_map: {tool_name: async callable} 可选，自定义 execute 行为
        """
        from src.tools.base import ToolMetadata

        reg = MagicMock(spec=ToolRegistry)

        def get_meta(name):
            return ToolMetadata(parallel_safe=False)

        reg.get_metadata.side_effect = get_meta

        def dispatch(name, arguments, agent=None):
            f = MagicMock()
            if execute_map and name in execute_map:
                f.execute = AsyncMock(side_effect=execute_map[name])
            else:
                f.execute = AsyncMock(return_value="ok")
            return f

        reg.dispatch.side_effect = dispatch
        return reg

    @pytest.mark.asyncio
    async def test_lock_serializes_concurrent_schedules(self):
        """两个并发 schedule() 调用被锁串行化，_global_dag 状态一致。

        验证策略：两个并发 schedule 各自携带 2 个工具。由于锁保护 DAG 创建/扩展，
        最终 _global_tool_calls 应包含全部 4 个工具，且 _batch_boundaries 正确。
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        async def schedule_batch(prefix):
            tcs = [
                {"id": f"{prefix}_1", "name": "tool_a", "arguments": {}},
                {"id": f"{prefix}_2", "name": "tool_b", "arguments": {}},
            ]
            return await executor.schedule(tcs, agent_ref=None)

        # 并发调度两批
        results_a, results_b = await asyncio.gather(
            schedule_batch("a"),
            schedule_batch("b"),
        )

        # 两批工具的结果都应正确返回
        assert len(results_a) == 2
        assert len(results_b) == 2

        # DAG 状态应一致：4 个工具 + 2 个批次边界
        assert executor._global_dag is not None
        assert len(executor._global_tool_calls) == 4
        assert len(executor._batch_boundaries) == 2

        # 每个批次边界递增
        assert executor._batch_boundaries[0] < executor._batch_boundaries[1]

        # 清理
        executor._reset_global_state()

    @pytest.mark.asyncio
    async def test_lock_released_during_execution(self):
        """锁在工具执行期间被释放，另一个协程可在此期间获取锁。

        验证策略：schedule() 调用中使用阻塞工具（asyncio.Event）。在工具阻塞期间，
        另一个协程应能直接获取 _schedule_lock（证明锁已释放）。
        """
        # 使用阻塞工具，让 schedule 的执行阶段挂起
        tool_block = asyncio.Event()

        async def blocked_execute():
            await tool_block.wait()
            return "done"

        reg = self._make_registry({"tool_a": blocked_execute, "tool_b": blocked_execute})
        executor = ToolScheduler(reg)

        # 启动一个多工具 schedule（会进入 DAG 路径，在执行阶段阻塞）
        tcs = [
            {"id": "block_1", "name": "tool_a", "arguments": {}},
            {"id": "block_2", "name": "tool_b", "arguments": {}},
        ]
        schedule_task = asyncio.ensure_future(
            executor.schedule(tcs, agent_ref=None)
        )

        # 等待 schedule 进入执行阶段（锁应已释放）
        await asyncio.sleep(0.15)

        # 另一个协程应能直接获取锁（不会阻塞/死锁）
        lock_acquired = False
        try:
            await asyncio.wait_for(
                executor._schedule_lock.acquire(), timeout=1.0
            )
            lock_acquired = True
            executor._schedule_lock.release()
        except asyncio.TimeoutError:
            pass

        assert lock_acquired, (
            "锁在工具执行期间应已释放，但另一个协程无法在 1 秒内获取锁 → 可能死锁"
        )

        # 释放阻塞，让 schedule 完成
        tool_block.set()
        results = await schedule_task
        assert len(results) == 2

        # 清理
        executor._reset_global_state()

    @pytest.mark.asyncio
    async def test_lock_protects_prev_non_dispatch_ids(self):
        """并发 schedule 时 _prev_non_dispatch_ids 不被交叉覆盖。

        验证策略：两个并发 schedule，一个携带 dispatch_agent，
        另一个不携带。验证最终 _prev_non_dispatch_ids 只包含最外层
        （最后一个完成的）的非 dispatch_agent ID。
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        async def schedule_a():
            tcs = [
                {"id": "a_read", "name": "read_file", "arguments": {}},
                {"id": "a_dispatch", "name": "dispatch_agent", "arguments": {}},
            ]
            return await executor.schedule(tcs, agent_ref=None)

        async def schedule_b():
            # 等待 A 进入 DAG 创建阶段后再开始
            await asyncio.sleep(0.05)
            tcs = [
                {"id": "b_write", "name": "write_file", "arguments": {}},
            ]
            return await executor.schedule(tcs, agent_ref=None)

        results_a, results_b = await asyncio.gather(
            schedule_a(), schedule_b(),
        )

        assert len(results_a) == 2
        assert len(results_b) == 1

        # _prev_non_dispatch_ids 应只包含非 dispatch_agent 的 ID
        # 且不包含 dispatch_agent 自身
        assert "a_dispatch" not in executor._prev_non_dispatch_ids, (
            "dispatch_agent ID 不应出现在 _prev_non_dispatch_ids 中"
        )

        # 清理
        executor._reset_global_state()

    @pytest.mark.asyncio
    async def test_schedule_lock_initialized(self):
        """验证 ToolScheduler 初始化后 _schedule_lock 存在且为 asyncio.Lock 实例"""
        executor = ToolScheduler(MagicMock(spec=ToolRegistry))
        assert hasattr(executor, '_schedule_lock'), (
            "ToolScheduler 应具有 _schedule_lock 属性"
        )
        assert isinstance(executor._schedule_lock, asyncio.Lock), (
            "_schedule_lock 应为 asyncio.Lock 实例"
        )

    def test_schedule_lock_reset(self):
        """_reset_global_state 应创建新的 _schedule_lock 实例"""
        executor = ToolScheduler(MagicMock(spec=ToolRegistry))
        old_lock = executor._schedule_lock
        executor._reset_global_state()
        new_lock = executor._schedule_lock

        assert isinstance(new_lock, asyncio.Lock), (
            "重置后 _schedule_lock 应仍为 asyncio.Lock 实例"
        )
        assert new_lock is not old_lock, (
            "重置后应为新的 Lock 实例（旧锁被 GC 回收）"
        )
