"""AsyncToolExecutor — 异步工具调用执行器

与同步版 ToolExecutor 接口对等，但：
- 工具执行使用 asyncio 原生 async/await（无额外线程池）
- dispatch_agent 使用 asyncio.Event 纯异步等待，不消耗线程池工人
- 不支持超时（所有工具等待到底，避免误杀长时间任务）
- 支持 asyncio.gather 实现真正的并发，**无并发数上限**（无限并发）
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple, Optional, Callable

from ..core.ports.tool_registry import ToolRegistryPort

# 延迟导入 ToolDAG（避免循环依赖）
# from .tool_dag import ToolDAG  — 在方法内延迟导入

_logger = logging.getLogger(__name__)


_MAX_CONCURRENT_TOOLS = 20  # 最大并发工具数，防止资源耗尽


class AsyncToolExecutor:
    """异步工具调用执行器，负责分派和执行工具调用。"""

    def __init__(self, registry: ToolRegistryPort):
        self.registry = registry
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)

    async def _run_tool_func(self, func, tc, run_method) -> tuple:
        """统一执行工具调用，处理 run_method 和直接执行两种路径

        Returns:
            (output, success) 元组
        """
        if run_method:
            coro = run_method(func, tc)
        else:
            coro = func.execute()
        result = await coro
        if run_method:
            if isinstance(result, tuple):
                return result[0], result[1]
            return result, True
        return result, True

    async def _execute_one_async(
        self,
        tc: dict,
        *,
        agent_ref,
        on_before: Optional[Callable],
        on_after: Optional[Callable],
        run_method: Optional[Callable],
    ) -> Tuple[str, str, bool]:
        """异步执行单个工具调用，无超时限制，等待到底。

        所有工具（含 dispatch_agent）统一使用 async/await 路径，
        dispatch_agent 内部使用 asyncio.Event 纯异步等待 barrier，
        不消耗任何线程池工人。
        """
        from ..ui.display import extract_key_params as _extract_key_params
        detail = _extract_key_params(tc["name"], tc["arguments"], show_all=True)

        if on_before:
            on_before(tc, detail)

        try:
            func = self.registry.dispatch(tc["name"], tc["arguments"], agent=agent_ref)
            output, success = await self._run_tool_func(func, tc, run_method)
            if on_after:
                on_after(tc, output, success)
            return (tc["id"], output, success)

        except asyncio.CancelledError:
            output = f"工具执行被取消: {tc['name']}"
            _logger.warning("Async tool %s cancelled", tc["name"])
            if on_after:
                on_after(tc, output, False)
            raise

        except Exception as e:
            output = f"工具执行失败: {e}"
            _logger.error("Async tool %s failed: %s", tc["name"], e, exc_info=True)

            if on_after:
                on_after(tc, output, False)

            return (tc["id"], output, False)

    async def execute_async(
        self,
        tool_calls: list,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
        parallel: bool = False,
    ) -> List[Tuple[str, str, bool]]:
        """异步执行工具调用列表。

        parallel=False 时所有工具串行执行。
        parallel=True 时自动根据工具 metadata 的 parallel_safe 字段分流：
        - parallel_safe=False 的工具先串行执行（保证安全顺序）
        - parallel_safe=True 的工具再并发执行（Semaphore 限流）
        - 最终按原始 tool_calls 顺序返回结果

        Args:
            tool_calls: 工具调用列表 [{"id", "name", "arguments"}]
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None  执行前回调
            on_after: (tc, output, success) -> None  执行后回调
            run_method: (func, tc) -> str  自定义执行方式
            parallel: 是否启用并行调度（自动 metadata 分流）

        Returns:
            [(tool_call_id, output, success)] 列表
        """
        if not parallel or len(tool_calls) <= 1:
            return await self._execute_serial(tool_calls, agent_ref=agent_ref, on_before=on_before, on_after=on_after, run_method=run_method)

        # 根据 parallel_safe metadata 自动分流
        serial_calls = []
        parallel_calls = []
        for tc in tool_calls:
            is_safe = False
            try:
                meta = self.registry.get_metadata(tc["name"])
                if meta is not None:
                    is_safe = meta.parallel_safe
            except Exception:
                _logger.debug("metadata 查询失败，工具 '%s' 默认串行执行", tc.get("name", "?"), exc_info=True)
                # 查询失败 → 默认串行（安全优先）

            if is_safe:
                parallel_calls.append(tc)
            else:
                serial_calls.append(tc)

        results_map = {}

        # 先串行执行非 parallel_safe 工具
        if serial_calls:
            serial_results = await self._execute_serial(serial_calls, agent_ref=agent_ref, on_before=on_before, on_after=on_after, run_method=run_method)
            for r in serial_results:
                results_map[r[0]] = r

        # 再并发执行 parallel_safe 工具
        if parallel_calls:
            parallel_results = await self._execute_concurrent(parallel_calls, agent_ref=agent_ref, on_before=on_before, on_after=on_after, run_method=run_method)
            for r in parallel_results:
                results_map[r[0]] = r

        # 按原始顺序返回
        return [results_map[tc["id"]] for tc in tool_calls]

    async def execute_dag_async(
        self,
        dag,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, str, bool]]:
        """按 DAG 拓扑层执行工具调用。

        1. 拓扑排序获取层列表
        2. 环检测：有环则回退到全串行
        3. 逐层执行：
           - 同层工具并发执行（asyncio.gather + Semaphore 限流）
           - 层内 FIRST_EXCEPTION 级联取消
           - 层间串行等待（上一层的输出是下一层的输入）
        4. 按原始 tool_calls 顺序返回结果

        Args:
            dag: ToolDAG 实例
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None  执行前回调
            on_after: (tc, output, success) -> None  执行后回调
            run_method: (func, tc) -> str  自定义执行方式

        Returns:
            [(tool_call_id, output, success)] 列表
        """
        if dag.size == 0:
            return []

        # 检查环
        if dag.has_cycle():
            _logger.warning("DAG cycle detected, falling back to serial execution")
            # 重建原始 tool_calls 列表
            all_calls = [{
                "id": tc_id,
                "name": dag.get_node(tc_id).name,
                "arguments": dag.get_node(tc_id).arguments,
            } for tc_id in dag._original_order]
            return await self._execute_serial(
                all_calls, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after, run_method=run_method,
            )

        # 获取拓扑层
        layers = dag.topological_sort()
        if layers is None:
            # 拓扑排序失败（理论上 has_cycle 已检测，兜底回退串行）
            all_calls = [{
                "id": tc_id,
                "name": dag.get_node(tc_id).name,
                "arguments": dag.get_node(tc_id).arguments,
            } for tc_id in dag._original_order]
            return await self._execute_serial(
                all_calls, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after, run_method=run_method,
            )

        # 逐层执行
        results_map: dict[str, tuple[str, str, bool]] = {}
        for layer in layers:
            if not layer:
                continue

            # 构建当前层的 tool_call dict 列表
            layer_calls = []
            for tc_id in layer:
                node = dag.get_node(tc_id)
                if node is None:
                    continue
                layer_calls.append({
                    "id": node.tc_id,
                    "name": node.name,
                    "arguments": node.arguments,
                })

            if not layer_calls:
                continue

            # 同层工具并发执行
            layer_results = await self._execute_concurrent(
                layer_calls,
                agent_ref=agent_ref,
                on_before=on_before,
                on_after=on_after,
                run_method=run_method,
            )
            for r in layer_results:
                results_map[r[0]] = r

        # 按原始顺序返回
        return [results_map[tc_id] for tc_id in dag._original_order
                if tc_id in results_map]

    async def _execute_serial(
        self, tool_calls: list, *,
        agent_ref, on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, str, bool]]:
        """串行执行工具调用列表，保持原始顺序，遇到取消则停止后续。"""
        results = []
        for tc in tool_calls:
            try:
                result = await self._execute_one_async(
                    tc, agent_ref=agent_ref, on_before=on_before,
                    on_after=on_after, run_method=run_method,
                )
            except asyncio.CancelledError:
                _logger.warning("Serial async tool %s cancelled, 停止后续工具", tc["name"])
                results.append((tc["id"], f"工具执行被取消: {tc['name']}", False))
                break
            except Exception as e:
                _logger.error("Serial async tool %s failed: %s", tc["name"], e)
                result = (tc["id"], f"工具执行失败: {e}", False)
                results.append(result)
                continue
            results.append(result)
        return results

    async def _execute_concurrent(
        self, tool_calls: list, *,
        agent_ref, on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, str, bool]]:
        """并发执行工具调用列表，使用 Semaphore 限流 + FIRST_EXCEPTION 级联取消。"""
        sem = self._semaphore

        async def _run_with_semaphore(tc):
            async with sem:
                return await self._execute_one_async(
                    tc,
                    agent_ref=agent_ref,
                    on_before=on_before,
                    on_after=on_after,
                    run_method=run_method,
                )

        tasks = {asyncio.ensure_future(_run_with_semaphore(tc)): tc for tc in tool_calls}
        results_map: dict[str, tuple[str, str, bool]] = {}

        try:
            while tasks:
                done, pending = await asyncio.wait(
                    tasks.keys(),
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                # 先收集所有已完成 task 的结果（含成功和失败），
                # 确保 FIRST_EXCEPTION 时其他同时完成的 task 不被遗漏。
                has_error = False
                for task in done:
                    tc = tasks[task]
                    try:
                        result = task.result()
                        results_map[tc["id"]] = result
                    except asyncio.CancelledError:
                        results_map[tc["id"]] = (tc["id"], f"工具执行被取消: {tc['name']}", False)
                    except Exception as e:
                        _logger.error("Parallel async tool %s failed: %s", tc["name"], e)
                        results_map[tc["id"]] = (tc["id"], f"工具执行失败: {e}", False)
                        has_error = True

                # 从 tasks 中移除已完成的
                for task in done:
                    del tasks[task]

                # 有失败时：取消所有剩余 pending 任务并收集结果
                if has_error and pending:
                    for pt in pending:
                        pt.cancel()
                    for pt in pending:
                        tc2 = tasks[pt]
                        try:
                            await pt
                        except asyncio.CancelledError:
                            results_map[tc2["id"]] = (tc2["id"], f"工具执行被级联取消: {tc2['name']}", False)
                        except Exception as e2:
                            results_map[tc2["id"]] = (tc2["id"], f"工具执行失败: {e2}", False)
                    tasks.clear()
        except asyncio.CancelledError:
            # 外部取消时，确保所有子任务被取消再重新抛出 CancelledError
            for task in list(tasks.keys()):
                if not task.done():
                    task.cancel()
            # 等待所有任务完成（含被取消的），return_exceptions=True 不传播异常
            await asyncio.gather(*tasks.keys(), return_exceptions=True)
            raise
        finally:
            # 兜底：确保没有任务残留（正常退出时 tasks 已为空）
            if tasks:
                for task in list(tasks.keys()):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks.keys(), return_exceptions=True)

        # 按原顺序返回结果
        return [results_map[tc["id"]] for tc in tool_calls]
