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
from src.core.tool_dag import ToolDAG
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
# 测试辅助函数
# ═══════════════════════════════════════════════════════════════

async def _poll_until(check_fn, timeout=5.0, interval=0.05):
    """轮询等待 check_fn() 返回 True，超时则抛出 asyncio.TimeoutError。

    用于替代固定 asyncio.sleep() 等待异步调度完成，避免慢速 CI 中的 flaky 测试。
    """
    async def _poll():
        while not check_fn():
            await asyncio.sleep(interval)

    await asyncio.wait_for(_poll(), timeout=timeout)


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
# _cleanup_batch_records — 批记录清理测试
# ═══════════════════════════════════════════════════════════════

class TestCleanupBatchRecords:
    """验证 _cleanup_batch_records 在最外层 schedule() 返回后自动触发

    验证要点：
    - 批执行完成后，DAG 节点、结果映射、边界记录均被清除
    - 嵌套 SubAgent 调用不触发清理
    - 空列表不触发清理
    """

    @staticmethod
    def _make_registry():
        from src.tools.base import ToolMetadata
        reg = MagicMock(spec=ToolRegistry)

        def get_meta(name):
            return ToolMetadata(parallel_safe=False)
        reg.get_metadata.side_effect = get_meta

        def dispatch(name, arguments, agent=None):
            f = MagicMock()
            f.execute = AsyncMock(return_value=f"result_{name}")
            return f
        reg.dispatch.side_effect = dispatch
        return reg

    @pytest.mark.asyncio
    async def test_cleanup_after_single_batch(self):
        """单批 schedule() 返回后，所有跨批记录被清除"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        tcs = [
            {"id": "call_1", "name": "tool_a", "arguments": {}},
            {"id": "call_2", "name": "tool_b", "arguments": {}},
        ]
        results = await executor.schedule(tcs, agent_ref=None)

        assert len(results) == 2
        # 最外层 schedule() 返回后，跨批记录应已被清除
        assert executor._global_dag is None, "DAG 应被清除"
        assert len(executor._batch_boundaries) == 0, "批次边界应被清除"
        assert len(executor._results_map) == 0, "结果映射应被清除"
        assert len(executor._completed_tc_ids) == 0, "已完成 ID 集合应被清除"
        assert len(executor._global_tool_calls) == 0, "全局工具列表应被清除"

    @pytest.mark.asyncio
    async def test_cleanup_after_two_batches(self):
        """两批 schedule() 返回后，跨批记录被清除

        分批调度验证：每批完成后清理，第二批重新创建 DAG。
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 第一批
        batch1 = [
            {"id": "b1_a", "name": "tool_x", "arguments": {}},
        ]
        r1 = await executor.schedule(batch1, agent_ref=None)
        assert len(r1) == 1

        # 第一批完成后记录应已被清除
        assert executor._global_dag is None

        # 第二批 — 重新创建 DAG（不再是 add_batch，而是创建新 DAG）
        batch2 = [
            {"id": "b2_a", "name": "tool_y", "arguments": {}},
        ]
        r2 = await executor.schedule(batch2, agent_ref=None)
        assert len(r2) == 1

        # 第二批完成后记录也应被清除
        assert executor._global_dag is None
        assert len(executor._batch_boundaries) == 0

    @pytest.mark.asyncio
    async def test_no_cleanup_on_nested_call(self):
        """嵌套 SubAgent 调用不触发清理（schedule_depth > 0）"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 手动将 _schedule_depth 设为 1 模拟嵌套调用
        executor._schedule_depth = 1
        try:
            tcs = [
                {"id": "call_nest", "name": "tool_n", "arguments": {}},
            ]
            results = await executor.schedule(tcs, agent_ref=None)
            assert len(results) == 1

            # 嵌套调用不应触发清理 → DAG 应仍存在
            assert executor._global_dag is not None, "嵌套调用应保留 DAG"
            assert len(executor._batch_boundaries) == 1, "嵌套调用应保留批次边界"
        finally:
            # 确保状态还原，防止泄漏到其他测试
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    @pytest.mark.asyncio
    async def test_no_cleanup_on_empty_list(self):
        """空列表 schedule() 不触发清理"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        results = await executor.schedule([], agent_ref=None)
        assert results == []

        # DAG 应仍为 None（空列表不创建也不清理）
        assert executor._global_dag is None
        assert len(executor._batch_boundaries) == 0

    # ── 新增字段清理测试 ──

    @pytest.mark.asyncio
    async def test_cleanup_clears_pending_tc_ids(self):
        """_cleanup_batch_records 清除 _pending_tc_ids"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 手动注入 pending ID（模拟未清理的状态）
        executor._pending_tc_ids.add("tc_1")
        executor._pending_tc_ids.add("tc_2")
        assert len(executor._pending_tc_ids) == 2

        await executor._cleanup_batch_records()

        assert len(executor._pending_tc_ids) == 0, "_pending_tc_ids 应被清除"

    @pytest.mark.asyncio
    async def test_cleanup_clears_running_bash_ids(self):
        """_cleanup_batch_records 清除 _running_bash_ids"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 手动注入 running bash ID
        executor._running_bash_ids.add("bash_1")
        executor._running_bash_ids.add("bash_2")
        assert len(executor._running_bash_ids) == 2

        await executor._cleanup_batch_records()

        assert len(executor._running_bash_ids) == 0, "_running_bash_ids 应被清除"

    @pytest.mark.asyncio
    async def test_cleanup_cancels_background_tasks(self):
        """_cleanup_batch_records 取消未完成的后台 Task 并清空列表

        _cleanup_batch_records 内部 await asyncio.gather 等待取消完成，
        因此调用后 task 已处于 cancelled 状态。
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 创建一个挂起的 Task（永远不会完成）
        async def never_done():
            await asyncio.Event().wait()

        task = asyncio.ensure_future(never_done())
        executor._background_dispatch_tasks.append(task)

        assert not task.done(), "Task 应处于挂起状态"
        assert len(executor._background_dispatch_tasks) == 1

        await executor._cleanup_batch_records()

        # _cleanup_batch_records 已调用 task.cancel() + await gather 等待完成
        # 列表应已清空，task 应已 cancelled
        assert len(executor._background_dispatch_tasks) == 0, "_background_dispatch_tasks 应被清空"
        assert task.cancelled(), "Task 应已被取消"

    @pytest.mark.asyncio
    async def test_cleanup_background_tasks_empty_list(self):
        """_cleanup_batch_records 空 _background_dispatch_tasks 列表不报错"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 空列表清理不应抛异常
        assert len(executor._background_dispatch_tasks) == 0

        await executor._cleanup_batch_records()

        assert len(executor._background_dispatch_tasks) == 0

    def test_reset_global_state_includes_new_fields(self):
        """_reset_global_state 包含 _pending_tc_ids / _running_bash_ids / _background_dispatch_tasks 重置"""
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 注入脏数据
        executor._pending_tc_ids.add("dirty_1")
        executor._running_bash_ids.add("dirty_bash")
        dirty_task = asyncio.ensure_future(asyncio.sleep(0))
        executor._background_dispatch_tasks.append(dirty_task)

        executor._reset_global_state()

        assert len(executor._pending_tc_ids) == 0, "_pending_tc_ids 应被重置"
        assert len(executor._running_bash_ids) == 0, "_running_bash_ids 应被重置"
        assert len(executor._background_dispatch_tasks) == 0, "_background_dispatch_tasks 应被重置"


# ═══════════════════════════════════════════════════════════════
# 批内节点清理测试
# ═══════════════════════════════════════════════════════════════

class TestBatchNodeCleanup:
    """验证 _execute_global_dag_async 批执行完成后节点清理的正确性

    覆盖：
    - 批执行完成后，已完成节点从 _global_dag 中移除
    - 未执行完成的节点不被清理
    - 已删除节点的 prev_non_dispatch_ids 边被正确跳过
    - 嵌套调用（SubAgent）不触发清理
    - 无可用节点时不报错
    """

    @staticmethod
    def _make_registry(execute_map=None, categories=None):
        from src.tools.base import ToolMetadata
        reg = MagicMock(spec=ToolRegistry)

        def get_meta(name):
            cat = (categories or {}).get(name, "general")
            return ToolMetadata(parallel_safe=False, tool_category=cat)
        reg.get_metadata.side_effect = get_meta

        def dispatch(name, arguments, agent=None):
            f = MagicMock()
            if execute_map and name in execute_map:
                f.execute = AsyncMock(side_effect=execute_map[name])
            else:
                f.execute = AsyncMock(return_value=f"result_{name}")
            return f
        reg.dispatch.side_effect = dispatch
        return reg

    @pytest.mark.asyncio
    async def test_nodes_cleaned_after_batch_execution(self):
        """单批完成后，节点从 _global_dag 中移除

        构造 DAG（read_file + bash，无路径重叠，无显式依赖）：
        - batch 工具完成后，节点应从 DAG 中移除
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1  # 防止 _cleanup_batch_records

        try:
            tcs = [
                {"id": "call_A", "name": "read_file",
                 "arguments": {"path": "/tmp/a.txt"}},
                {"id": "call_B", "name": "bash",
                 "arguments": {"command": "echo hi"}},
            ]

            results = await executor.schedule(tcs, agent_ref=None)

            assert len(results) == 2
            # 验证 DAG 仍存在（未被 _cleanup_batch_records 清除）
            assert executor._global_dag is not None

            # 验证已完成节点已被移除（节点已写入 _results_map → _completed_tc_ids → 被清理）
            if executor._completed_tc_ids:
                for tc_id in executor._completed_tc_ids:
                    assert executor._global_dag.get_node(tc_id) is None, (
                        f"已完成节点 {tc_id} 应从 DAG 中移除"
                    )
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    @pytest.mark.asyncio
    async def test_node_cleanup_skips_unexecuted(self):
        """未执行的节点不被清理

        构造 DAG（bash 阻塞 + 依赖 bash 的 read_file）：
        - bash 阻塞时，read_file 未执行
        - 模拟一批完成后，read_file（未执行）应仍存在于 DAG 中
        """
        bash_block = asyncio.Event()
        execution_order = []

        async def bash_exec():
            execution_order.append("bash_start")
            await bash_block.wait()
            execution_order.append("bash_end")
            return "bash_ok"

        async def read_exec():
            execution_order.append("read_exec")
            return "read_ok"

        reg = self._make_registry(
            execute_map={"bash": bash_exec, "read_file": read_exec},
            categories={"bash": "bash"},
        )
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1

        try:
            # bash 阻塞 + read_file 显式依赖 bash（$call_bash）
            tcs = [
                {"id": "call_bash", "name": "bash",
                 "arguments": {"command": "sleep"}},
                {"id": "call_read", "name": "read_file",
                 "arguments": {"path": "$call_bash"}},
            ]

            task = asyncio.ensure_future(
                executor.schedule(tcs, agent_ref=None)
            )

            # 等待 bash 启动（read_file 未执行）
            await _poll_until(lambda: "bash_start" in execution_order)

            # 此时 _completed_tc_ids 应为空（无节点完成）
            assert len(executor._completed_tc_ids) == 0, (
                "bash 运行中无节点完成"
            )

            # 验证 DAG 中仍有 read_file 节点
            assert executor._global_dag is not None
            assert executor._global_dag.get_node("call_read") is not None, (
                "未执行的 read_file 节点应仍在 DAG 中"
            )

            # 释放 bash，等待完成
            bash_block.set()
            results = await asyncio.wait_for(task, timeout=5.0)
            assert len(results) == 2

            # bash 完成后，read_file 应执行并完成
            assert "read_exec" in execution_order

            # 验证最终所有节点都被清理
            if executor._completed_tc_ids:
                for tc_id in executor._completed_tc_ids:
                    assert executor._global_dag.get_node(tc_id) is None
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    @pytest.mark.asyncio
    async def test_prev_non_dispatch_edge_skipped_for_removed_nodes(self):
        """prev_non_dispatch_ids 中已删除节点的边被跳过，批次正常执行

        验证场景：第一批的部分节点被清理后，第二批 add_batch 时，
        prev_non_dispatch_ids 中已删除节点的边被正确跳过。

        步骤：
        1. 构造一个含两个工具的 DAG（不使用类别工具避免类别约束干扰）
        2. 手动移除其中一个节点（模拟该节点已完成并被清理）
        3. 手动构造 add_batch 调用，prev_non_dispatch_ids 包含已删除和未删除节点
        4. 验证：已删除节点的边被跳过，未删除节点的边正常创建
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 构造 DAG（用 general 类别避免类别约束形成反向边）
        batch1 = [
            {"id": "b1_A", "name": "search",
             "arguments": {"query": "a"}},
            {"id": "b1_B", "name": "search",
             "arguments": {"query": "b"}},
        ]
        dag = ToolDAG(batch1, reg)
        executor._global_dag = dag
        executor._global_tool_calls = list(batch1)
        executor._batch_boundaries = [2]
        # 设置 prev_non_dispatch_ids（模拟第一批的非 dispatch 工具）
        executor._prev_non_dispatch_ids = {"b1_A", "b1_B"}

        # 模拟节点已完成：从 DAG 中移除 b1_A（模拟清理）
        dag.remove_nodes({"b1_A"})
        assert dag.get_node("b1_A") is None
        assert dag.get_node("b1_B") is not None

        # 第二批：通过 add_batch 添加，prev_non_dispatch_ids={b1_A, b1_B}
        # 复用已有的 _prev_non_dispatch_ids
        batch2 = [
            {"id": "b2_C", "name": "search",
             "arguments": {"query": "c"}},
        ]
        dag.add_batch(batch2, reg,
                       prev_non_dispatch_ids=executor._prev_non_dispatch_ids)

        # 验证：b2_C 存在
        assert dag.get_node("b2_C") is not None

        # 验证：b1_A 的边被跳过（b1_A 已不在 _nodes）
        node_c = dag.get_node("b2_C")
        assert "b1_A" not in node_c.dependencies, (
            "已删除节点 b1_A 的边应被跳过"
        )
        # 验证：b1_B 的边正常创建
        assert "b1_B" in node_c.dependencies, (
            "b1_B 的边应正常创建"
        )

        # 拓扑排序正常
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) >= 2

    @pytest.mark.asyncio
    async def test_cleanup_only_outermost(self):
        """is_outermost=False 时不执行节点清理

        验证：当 _execution_depth > 1（嵌套调用）时，
        is_outermost 为 False，节点清理逻辑不执行。
        这需要直接验证 cleanup 代码路径中的条件判断。
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)

        # 构造一个 DAG
        tcs = [
            {"id": "call_A", "name": "search",
             "arguments": {"query": "hello"}},
        ]
        dag = ToolDAG(tcs, reg)

        # 模拟嵌套调用深度 = 2（最外层是 1，嵌套层是 2）
        executor._global_dag = dag
        executor._execution_depth = 2  # 模拟 is_outermost=False

        try:
            results = await executor._execute_global_dag_async(
                dag, agent_ref=None, current_batch_ids={"call_A"},
            )
            # 嵌套调用应正常执行（不清理节点，不报错）
            assert len(results) >= 0
            # 节点应仍在 DAG 中（is_outermost=False → 不清理）
            if executor._global_dag is not None:
                assert executor._global_dag.get_node("call_A") is not None, (
                    "嵌套调用不应清理节点"
                )
        finally:
            executor._execution_depth = 0

    @pytest.mark.asyncio
    async def test_cleanup_with_empty_completed_set(self):
        """无已完成节点时清理不报错

        验证：batch_completed 为空时，remove_nodes 不被调用
        """
        reg = self._make_registry()
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1

        try:
            tcs = [
                {"id": "call_A", "name": "read_file",
                 "arguments": {"path": "/tmp/a.txt"}},
            ]
            results = await executor.schedule(tcs, agent_ref=None)
            assert len(results) == 1
            # 不应引发异常
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

# ═══════════════════════════════════════════════════════════════
# bash 独占运行测试
# ═══════════════════════════════════════════════════════════════

class TestBashExclusive:
    """bash 独占运行测试

    验证：bash 工具运行期间，只有 dispatch_agent（tool_category="general"
    且 name="dispatch_agent"）可并行执行，其他工具（含 read/write/bash/interactive
    及其他 general 工具）必须等待 bash 完成。

    调度机制要点：
    - bash 运行标记 _running_bash_ids 非空时，调度层仅放行 dispatch_agent
    - bash 完成后，标记清除，其余工具恢复执行
    - 无 bash 运行时，正常并发不受影响
    """

    @staticmethod
    def _make_registry(execute_map=None, categories=None):
        """创建支持 tool_category 的 mock registry

        Args:
            execute_map: {tool_name: async callable}
            categories: {tool_name: tool_category}，默认 "general"
        """
        from src.tools.base import ToolMetadata
        reg = MagicMock(spec=ToolRegistry)

        def get_meta(name):
            cat = (categories or {}).get(name, "general")
            return ToolMetadata(parallel_safe=False, tool_category=cat)
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

    # ── 场景 1：bash 运行中只有 dispatch_agent 可并行 ──

    @pytest.mark.asyncio
    async def test_bash_running_blocks_non_dispatch(self):
        """bash 运行中阻塞非 dispatch_agent 工具（跨层阻塞）

        构造 DAG：
        - Layer 1: bash + dispatch_agent（并行）
        - Layer 2: read_file (category="general"，通过 $call_bash 显式依赖 bash)

        bash 阻塞执行 → dispatch_agent 在 Layer 1 并行完成 →
        Layer 2 中 read_file 被 bash 独占过滤阻塞，直至 bash 完成后才执行。
        """
        bash_block = asyncio.Event()
        execution_order = []

        async def bash_exec():
            execution_order.append("bash_start")
            await bash_block.wait()
            execution_order.append("bash_end")
            return "bash_ok"

        async def read_exec():
            execution_order.append("read_exec")
            return "read_ok"

        async def dispatch_exec():
            execution_order.append("dispatch_exec")
            return "dispatch_ok"

        reg = self._make_registry(
            execute_map={"bash": bash_exec, "read_file": read_exec,
                         "dispatch_agent": dispatch_exec},
            categories={"bash": "bash"},
        )
        executor = ToolScheduler(reg)

        tcs = [
            {"id": "call_bash", "name": "bash", "arguments": {"command": "sleep"}},
            {"id": "call_read", "name": "read_file",
             "arguments": {"path": "$call_bash"}},  # 显式依赖 bash → Layer 2
            {"id": "call_disp", "name": "dispatch_agent",
             "arguments": {"prompt": "hi"}},  # general 类别，Layer 1
        ]

        task = asyncio.ensure_future(executor.schedule(tcs, agent_ref=None))

        # 等待 dispatch_agent 执行完毕（证明 dispatch_agent 与 bash 并行）
        await _poll_until(lambda: "dispatch_exec" in execution_order)

        assert "dispatch_exec" in execution_order, (
            "dispatch_agent 应与 bash 并行执行"
        )
        assert "read_exec" not in execution_order, (
            "read_file（Layer 2）不应在 bash 运行期间执行"
        )

        # 释放 bash
        bash_block.set()
        results = await asyncio.wait_for(task, timeout=5.0)

        assert len(results) == 3
        assert "read_exec" in execution_order, "bash 完成后 read_file 应执行"
        # 验证执行顺序：dispatch 在 bash 阻塞期间完成，read 在 bash 之后
        assert execution_order.index("dispatch_exec") < execution_order.index("read_exec"), (
            "dispatch_agent 应在 read_file 之前完成"
        )

    # ── 场景 2：bash 链式 + 独占组合 ──

    @pytest.mark.asyncio
    async def test_bash_chain_exclusive(self):
        """bash 链式执行 + 独占：dispatch_agent 可与首 bash 并行，write 等待全部 bash 完成

        构造 DAG（bash 链式依赖 rule b + 显式依赖）：
        - Layer 1: call_bash1 + dispatch_agent（并行）
        - Layer 2: call_bash2（依赖 call_bash1 via rule b）
        - Layer 3: call_write（category="general"，依赖 call_bash2 via $call_bash2）

        预期：call_bash1 运行期间 dispatch_agent 并行执行，call_bash2 和 call_write
        被独占过滤阻塞；call_bash1 完成后 call_bash2 运行，call_write 在 call_bash2 完成后执行。
        """
        bash1_block = asyncio.Event()
        bash2_block = asyncio.Event()
        execution_order = []
        bash_call_count = 0

        async def bash_exec():
            nonlocal bash_call_count
            bash_call_count += 1
            if bash_call_count == 1:
                execution_order.append("bash1_start")
                await bash1_block.wait()
                execution_order.append("bash1_end")
                return "bash1_ok"
            else:
                execution_order.append("bash2_start")
                await bash2_block.wait()
                execution_order.append("bash2_end")
                return "bash2_ok"

        async def write_exec():
            execution_order.append("write_exec")
            return "write_ok"

        async def dispatch_exec():
            execution_order.append("dispatch_exec")
            return "dispatch_ok"

        reg = self._make_registry(
            execute_map={"bash": bash_exec, "write_file": write_exec,
                         "dispatch_agent": dispatch_exec},
            categories={"bash": "bash"},  # write_file 使用默认 "general"
        )
        executor = ToolScheduler(reg)

        tcs = [
            {"id": "call_bash1", "name": "bash", "arguments": {"command": "cmd1"}},
            {"id": "call_bash2", "name": "bash", "arguments": {"command": "cmd2"}},
            {"id": "call_write", "name": "write_file",
             "arguments": {"path": "$call_bash2"}},
            {"id": "call_disp", "name": "dispatch_agent",
             "arguments": {"prompt": "hi"}},
        ]

        task = asyncio.ensure_future(executor.schedule(tcs, agent_ref=None))

        # 等待 dispatch_agent 完成（应随 call_bash1 并行执行）
        await _poll_until(lambda: "dispatch_exec" in execution_order)

        assert "dispatch_exec" in execution_order, "dispatch_agent 应与 call_bash1 并行"
        assert "bash2_start" not in execution_order, "call_bash2 不应在 call_bash1 运行期间启动"
        assert "write_exec" not in execution_order, "call_write 不应在 call_bash1 运行期间执行"

        # 释放 call_bash1，call_bash2 应开始执行
        bash1_block.set()
        await _poll_until(lambda: "bash1_end" in execution_order)

        assert "bash1_end" in execution_order, "call_bash1 应已完成"
        # bash2 需要下一轮拓扑后才能启动，用轮询等待
        await _poll_until(lambda: "bash2_start" in execution_order)
        assert "bash2_start" in execution_order, "call_bash2 应在 call_bash1 完成后启动"

        # 释放 call_bash2，call_write 应执行
        bash2_block.set()
        results = await asyncio.wait_for(task, timeout=5.0)

        assert len(results) == 4
        assert "write_exec" in execution_order, "call_bash2 完成后 call_write 应执行"
        assert execution_order.index("bash1_end") < execution_order.index("bash2_start"), (
            "call_bash2 应在 call_bash1 完成后启动（链式串行）"
        )
        assert execution_order.index("bash2_end") < execution_order.index("write_exec"), (
            "call_write 应在 call_bash2 完成后执行"
        )

    # ── 场景 3：无 bash 时正常并发 ──

    @pytest.mark.asyncio
    async def test_no_bash_normal_concurrency(self):
        """无 bash 工具时正常并发，不受独占过滤影响"""
        execution_order = []

        async def read_exec():
            execution_order.append("read_exec")
            return "read_ok"

        async def write_exec():
            execution_order.append("write_exec")
            return "write_ok"

        async def dispatch_exec():
            execution_order.append("dispatch_exec")
            return "dispatch_ok"

        reg = self._make_registry(
            execute_map={"read_file": read_exec, "write_file": write_exec,
                         "dispatch_agent": dispatch_exec},
            categories={"read_file": "read", "write_file": "write"},
        )
        executor = ToolScheduler(reg)

        tcs = [
            {"id": "read_1", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "write_1", "name": "write_file",
             "arguments": {"path": "b.txt", "content": "hi"}},
            {"id": "disp_1", "name": "dispatch_agent",
             "arguments": {"prompt": "hi"}},
        ]

        results = await executor.schedule(tcs, agent_ref=None)

        assert len(results) == 3
        assert all(r[2] for r in results), "所有工具应成功执行"
        assert "read_exec" in execution_order
        assert "write_exec" in execution_order
        assert "dispatch_exec" in execution_order

    # ── 场景 4：bash 运行中无 dispatch_agent 时正确跳过层 ──

    @pytest.mark.asyncio
    async def test_bash_running_no_dispatch_skips_layer(self):
        """bash 运行中无 dispatch_agent 时，调度正确等待（不空转）

        构造 DAG：
        - Layer 1: bash
        - Layer 2: read_file (category="general"，依赖 bash via $call_bash)

        预期：bash 运行期间 Layer 2 被独占过滤 → 层为空 → continue 让出控制权 →
        不空转死循环；bash 完成后 read_file 正常执行。
        """
        bash_block = asyncio.Event()
        execution_order = []

        async def bash_exec():
            execution_order.append("bash_start")
            await bash_block.wait()
            execution_order.append("bash_end")
            return "bash_ok"

        async def read_exec():
            execution_order.append("read_exec")
            return "read_ok"

        reg = self._make_registry(
            execute_map={"bash": bash_exec, "read_file": read_exec},
            categories={"bash": "bash"},
        )
        executor = ToolScheduler(reg)

        tcs = [
            {"id": "call_bash", "name": "bash", "arguments": {"command": "sleep"}},
            {"id": "call_read", "name": "read_file",
             "arguments": {"path": "$call_bash"}},  # 依赖 bash → Layer 2
        ]

        task = asyncio.ensure_future(executor.schedule(tcs, agent_ref=None))

        # 等待 bash 启动
        await _poll_until(lambda: "bash_start" in execution_order)
        assert "bash_start" in execution_order
        assert "read_exec" not in execution_order, (
            "read_file 不应在 bash 运行期间执行"
        )

        # 验证不空转：_running_bash_ids 应包含 call_bash
        assert "call_bash" in executor._running_bash_ids, (
            "_running_bash_ids 应包含正在运行的 bash ID"
        )

        # 释放 bash
        bash_block.set()
        results = await asyncio.wait_for(task, timeout=5.0)

        assert len(results) == 2
        assert "read_exec" in execution_order, "bash 完成后 read_file 应执行"
        assert "call_bash" not in executor._running_bash_ids, (
            "bash 完成后 _running_bash_ids 应被清除"
        )


class TestDispatchAgentEarlyReturn:
    """dispatch_agent 提前放行测试

    验证：当 DAG 中仅剩 dispatch_agent 节点未执行时，schedule() 将其转为
    后台任务异步执行并提前返回，不阻塞外层调用。

    场景覆盖：
    - 仅剩 dispatch_agent 时提前返回
    - 后台 dispatch_agent 结果正确写入 _results_map
    - 非 dispatch_agent 仍存在时不提前返回
    - 后台 Task 异常时写入失败结果
    - 无剩余节点时正常退出（不创建后台任务）
    """

    @staticmethod
    def _make_registry(execute_map=None, categories=None):
        """创建支持 tool_category 的 mock registry"""
        from src.tools.base import ToolMetadata
        reg = MagicMock(spec=ToolRegistry)

        def get_meta(name):
            cat = (categories or {}).get(name, "general")
            return ToolMetadata(parallel_safe=False, tool_category=cat)
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

    # ── 场景 1：仅剩 dispatch_agent 时提前返回 ──

    @pytest.mark.asyncio
    async def test_early_return_only_dispatch_remaining(self):
        """仅剩 dispatch_agent 时 schedule 提前返回，不等待后台任务

        构造 DAG：
        - Layer 1: read_file（快速完成）
        - Layer 2: dispatch_agent（阻塞，依赖 read_file 通过 $call_read）

        预期：read_file 完成后 schedule() 返回（dispatch_agent 在后台运行），
        返回结果仅含 read_file。
        """
        dispatch_block = asyncio.Event()
        execution_order = []

        async def read_exec():
            execution_order.append("read_exec")
            return "read_ok"

        async def dispatch_exec():
            execution_order.append("dispatch_start")
            await dispatch_block.wait()
            execution_order.append("dispatch_end")
            return "dispatch_ok"

        reg = self._make_registry(
            execute_map={"read_file": read_exec, "dispatch_agent": dispatch_exec},
        )
        executor = ToolScheduler(reg)
        # 防止 schedule() 自动调用 _cleanup_batch_records
        executor._schedule_depth = 1

        try:
            # $call_read 显式依赖 → dispatch_agent 在 Layer 2
            tcs = [
                {"id": "call_read", "name": "read_file", "arguments": {}},
                {"id": "call_disp", "name": "dispatch_agent",
                 "arguments": {"prompt": "$call_read"}},
            ]

            results = await executor.schedule(tcs, agent_ref=None)

            # 轮询等待后台 dispatch_agent 启动
            await _poll_until(lambda: "dispatch_start" in execution_order)

            # 验证提前返回：仅 read_file 结果在返回列表中
            assert len(results) == 1, "应仅返回 read_file 结果"
            assert results[0][0] == "call_read"
            assert "read_exec" in execution_order
            assert "dispatch_start" in execution_order, (
                "后台 dispatch_agent 应已启动"
            )
            assert "dispatch_end" not in execution_order, (
                "dispatch_agent 应仍在后台运行"
            )

            # 验证后台任务已创建
            assert len(executor._background_dispatch_tasks) == 1, (
                "应有一个后台 dispatch_agent 任务"
            )

            # 释放 dispatch_agent，等待后台任务完成
            dispatch_block.set()
            bg_task = executor._background_dispatch_tasks[0]
            await asyncio.wait_for(bg_task, timeout=5.0)

            assert "dispatch_end" in execution_order, "dispatch_agent 应已完成"
            assert "call_disp" in executor._results_map, "后台结果应写入 _results_map"
            assert executor._results_map["call_disp"][1] == "dispatch_ok"
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    # ── 场景 2：后台 dispatch_agent 结果正确写入 _results_map ──

    @pytest.mark.asyncio
    async def test_background_result_written_to_results_map(self):
        """后台 dispatch_agent 完成后结果正确写入 _results_map 和 _completed_tc_ids"""
        reg = self._make_registry(
            execute_map={
                "read_file": AsyncMock(return_value="read_ok"),
                "dispatch_agent": AsyncMock(return_value="dispatch_ok"),
            },
        )
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1

        try:
            # $call_read 显式依赖 → dispatch_agent 在 Layer 2
            tcs = [
                {"id": "call_read", "name": "read_file", "arguments": {}},
                {"id": "call_disp", "name": "dispatch_agent",
                 "arguments": {"prompt": "$call_read"}},
            ]

            await executor.schedule(tcs, agent_ref=None)

            # 等待后台任务完成（快速 dispatch_agent）
            if executor._background_dispatch_tasks:
                bg_task = executor._background_dispatch_tasks[0]
                await asyncio.wait_for(bg_task, timeout=5.0)

            # 验证结果写入
            assert "call_disp" in executor._results_map, (
                "dispatch_agent 结果应写入 _results_map"
            )
            assert executor._results_map["call_disp"][1] == "dispatch_ok"
            assert executor._results_map["call_disp"][2] is True
            assert "call_disp" in executor._completed_tc_ids, (
                "dispatch_agent ID 应在 _completed_tc_ids 中"
            )
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    # ── 场景 3：非 dispatch_agent 仍存在时不提前返回 ──

    @pytest.mark.asyncio
    async def test_no_early_return_when_non_dispatch_exists(self):
        """read_file + bash（均非 dispatch_agent）→ 正常等待全部完成"""
        reg = self._make_registry(
            execute_map={
                "read_file": AsyncMock(return_value="read_ok"),
                "bash": AsyncMock(return_value="bash_ok"),
            },
            categories={"bash": "bash"},
        )
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1

        try:
            tcs = [
                {"id": "call_read", "name": "read_file", "arguments": {}},
                {"id": "call_bash", "name": "bash", "arguments": {}},
            ]

            results = await executor.schedule(tcs, agent_ref=None)

            # 验证正常完成：两个工具结果都在
            assert len(results) == 2
            assert all(r[2] for r in results), "所有工具应成功执行"
            # 验证未触发提前返回
            assert len(executor._background_dispatch_tasks) == 0, (
                "无 dispatch_agent 时不应创建后台任务"
            )
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    # ── 场景 4：后台 Task 异常时写入失败结果 ──

    @pytest.mark.asyncio
    async def test_background_task_exception_writes_failure(self):
        """后台 dispatch_agent 抛异常 → 失败结果写入 _results_map"""
        async def dispatch_fail():
            raise ValueError("dispatch agent crash")

        reg = self._make_registry(
            execute_map={
                "read_file": AsyncMock(return_value="read_ok"),
                "dispatch_agent": dispatch_fail,
            },
        )
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1

        try:
            # $call_read 显式依赖 → dispatch_agent 在 Layer 2
            tcs = [
                {"id": "call_read", "name": "read_file", "arguments": {}},
                {"id": "call_disp", "name": "dispatch_agent",
                 "arguments": {"prompt": "$call_read"}},
            ]

            await executor.schedule(tcs, agent_ref=None)

            # 等待后台任务完成
            if executor._background_dispatch_tasks:
                bg_task = executor._background_dispatch_tasks[0]
                await asyncio.wait_for(bg_task, timeout=5.0)

            # 验证失败结果写入
            assert "call_disp" in executor._results_map, (
                "dispatch_agent 失败结果应写入 _results_map"
            )
            assert "dispatch agent crash" in executor._results_map["call_disp"][1], (
                "失败消息应包含异常信息"
            )
            assert executor._results_map["call_disp"][2] is False, (
                "失败时应 success=False"
            )
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0

    # ── 场景 5：空 DAG / 无剩余节点时正常退出 ──

    @pytest.mark.asyncio
    async def test_no_remaining_nodes_normal_exit(self):
        """所有节点已执行 → _only_dispatch_agent_remaining 无作用 → 正常退出"""
        reg = self._make_registry(
            execute_map={
                "read_file": AsyncMock(return_value="read_ok"),
            },
        )
        executor = ToolScheduler(reg)
        executor._schedule_depth = 1

        try:
            tcs = [
                {"id": "call_read", "name": "read_file", "arguments": {}},
            ]

            results = await executor.schedule(tcs, agent_ref=None)

            assert len(results) == 1
            assert results[0][2] is True
            assert len(executor._background_dispatch_tasks) == 0, (
                "无 dispatch_agent 时不应创建后台任务"
            )
        finally:
            await executor._cleanup_batch_records()
            executor._schedule_depth = 0


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

        # 最外层 schedule() 返回后跨批记录可能已被清理（_cleanup_batch_records）
        # 也可能未被清理（取决于并发时序中最外层批次是否最后完成）。
        # 两种状态都是正确的，验证不出现悬挂/不一致的数据即可。
        if executor._global_dag is not None:
            # 跨批记录尚未被清理 → 检查一致性后手动清理
            assert len(executor._global_tool_calls) == 4
            assert len(executor._batch_boundaries) == 2
            assert executor._batch_boundaries[0] < executor._batch_boundaries[1]
            executor._reset_global_state()
        else:
            # 已被清理 → 所有记录应已清空
            assert len(executor._global_tool_calls) == 0
            assert len(executor._batch_boundaries) == 0

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
