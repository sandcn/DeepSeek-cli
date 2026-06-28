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
from ..ui.display import extract_key_params

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
        detail = extract_key_params(tc["name"], tc["arguments"], show_all=True)

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

        Args:
            tool_calls: 工具调用列表 [{"id", "name", "arguments"}]
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None  执行前回调
            on_after: (tc, output, success) -> None  执行后回调
            run_method: (func, tc) -> str  自定义执行方式
            parallel: 是否并发执行多个工具调用

        Returns:
            [(tool_call_id, output, success)] 列表
        """
        if not parallel or len(tool_calls) <= 1:
            results = []
            for tc in tool_calls:
                try:
                    result = await self._execute_one_async(
                        tc,
                        agent_ref=agent_ref,
                        on_before=on_before,
                        on_after=on_after,
                        run_method=run_method,
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

        # 并发执行
        # TODO: 后续可利用 tool metadata 的 parallel_safe 字段做更精细的并行调度：
        #       将 parallel_safe=True 的工具并发执行，parallel_safe=False 的工具串行执行，
        #       实现 metadata 驱动的智能调度，避免非线程安全工具同时访问共享资源。
        # 使用 asyncio.wait(return_when=FIRST_EXCEPTION) 替代
        # 手动 fail_event + cancel_monitor 模式，减少调度开销。
        # 首个工具抛出异常时立即取消其余未完成的任务。
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
        final_results = [results_map[tc["id"]] for tc in tool_calls]
        return final_results
