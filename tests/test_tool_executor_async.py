"""Tests for src/core/tool_executor_async.py — ToolScheduler

覆盖内容（按执行路径）：
  1. 构造函数与属性
  2. _execute_one_async 单工具执行（成功/异常/Cancelled/回调/run_method）
  3. 取消与清理
  4. schedule() — 统一调度入口（全局 DAG 单工具/多工具）
  5. schedule() — asyncio.Lock 并发保护
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
# _execute_concurrent — 并发模式
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# 取消与清理
# ═══════════════════════════════════════════════════════════════

class TestCancellationCleanup:
    """取消后的 task 清理验证"""

    @pytest.mark.asyncio
    async def test_schedule_cancelled(self, executor, tool_calls):
        """schedule() 中被取消 → CancelledError 被 re-raise 到上层"""
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
            executor.schedule(tool_calls[:2], agent_ref=None)
        )

        await asyncio.sleep(0.05)
        task.cancel()

        # schedule 中 CancelledError 被 re-raise
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_parallel_cancel_monitor_cleanup(self, executor, tool_calls):
        """schedule() 完成时清理正确"""
        task = asyncio.ensure_future(
            executor.schedule(tool_calls, agent_ref=None)
        )

        results = await task
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_return_exceptions_not_leaked(self, executor, mock_func, tool_calls):
        """schedule() 异常不会泄漏到外部"""
        mock_func.execute.side_effect = ValueError("fail")

        results = await executor.schedule(
            tool_calls, agent_ref=None,
        )

        assert len(results) == 3
        for r in results:
            assert isinstance(r, tuple)
            assert len(r) == 3
            assert r[2] is False


# ═══════════════════════════════════════════════════════════════
# parallel_safe metadata 自动分流
# ═══════════════════════════════════════════════════════════════

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
        """单工具 → 走全局 DAG 路径"""
        results = await executor.schedule([tool_call], agent_ref=None)
        assert len(results) == 1
        assert results[0] == ("call_1", "ok", True)
        mock_func.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_schedule_multi(self, executor, mock_func, tool_calls):
        """多工具 → 走全局 DAG 路径"""
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
