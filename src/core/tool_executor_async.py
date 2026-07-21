"""ToolScheduler — 全局工具调用调度器

统一调度入口，内聚 ToolDAG 构建 + 调度 + 并发控制，为 Main Agent 和所有 SubAgent
提供同一全局单例。

设计要点：
- 工具执行使用 asyncio 原生 async/await（无额外线程池）
- dispatch_agent 使用 asyncio.Event 纯异步等待，不消耗线程池工人
- 不支持超时（所有工具等待到底，避免误杀长时间任务）
- 支持 asyncio.gather 实现真正的并发
- Semaphore 为类级全局单例，跨 Agent 实例共享限流
- module-level `_default_scheduler` 单例 + `ToolScheduler.default()` 获取
- `schedule()` 为统一且唯一的入口：所有工具都走全局 DAG 路径
- 多批并发：批间串行执行，上一批只剩 dispatch_agent 时下一批可并行
- SubAgent 工具调用同样走全局 DAG 路径
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple, Optional, Callable

from ..tools.registry import ToolRegistry
from ..tui.core.param_formatter import extract_key_params
from .tool_dag import ToolDAG

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------
#  全局单例引用（懒初始化）
# ------------------------------------------------------------
_default_scheduler: Optional[ToolScheduler] = None

_MAX_CONCURRENT_TOOLS = 0  # 最大并发工具数，0 表示无限制


class ToolScheduler:
    """全局工具调用调度器，负责构建 ToolDAG、调度和执行工具调用。

    通过 ToolScheduler.default() 获取模块级单例，所有 Agent/SubAgent
    共享同一实例和 Semaphore 限流器。
    """

    # 类级 Semaphore（所有实例共享）
    _semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self, registry: Optional[ToolRegistry] = None):
        """初始化调度器。

        Args:
            registry: 工具注册表，None 时使用 ToolRegistry.default()。
                      保留可选参数用于测试注入 mock registry。
        """
        self._registry = registry or ToolRegistry.default()

        # 类级 Semaphore 懒初始化（跨所有实例共享，0 表示无限制）
        if ToolScheduler._semaphore is None and _MAX_CONCURRENT_TOOLS > 0:
            ToolScheduler._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_TOOLS)

        # ── 全局 DAG 状态（跨多批调度持久化） ──────────────
        self._global_dag: Optional[ToolDAG] = None
        self._global_tool_calls: list[dict] = []
        self._batch_boundaries: list[int] = []
        self._prev_non_dispatch_ids: set[str] = set()
        self._results_map: dict[str, tuple[str, str, bool]] = {}
        self._completed_tc_ids: set[str] = set()
        self._pending_tc_ids: set[str] = set()
        self._execution_depth: int = 0  # _execute_global_dag_async 嵌套深度
        # ── 双重保护机制（分工协作） ─────────────────────
        # _schedule_lock：asyncio.Lock 防并发竞态 — 多 SubAgent 通过
        #   asyncio.gather 并发调用 schedule() 时保护共享状态修改。
        # _schedule_depth：int 防语义污染 — SubAgent 嵌套调用时只有
        #   最外层 MainAgent 的调用才更新 _prev_non_dispatch_ids。
        self._schedule_depth: int = 0
        self._schedule_lock = asyncio.Lock()

    @classmethod
    def default(cls) -> ToolScheduler:
        """返回模块级默认 ToolScheduler 实例（单例模式）"""
        global _default_scheduler
        if _default_scheduler is None:
            _default_scheduler = cls()
        return _default_scheduler

    def _reset_global_state(self) -> None:
        """重置全局 DAG 状态（供测试清理使用）"""
        self._global_dag = None
        self._global_tool_calls.clear()
        self._batch_boundaries.clear()
        self._prev_non_dispatch_ids.clear()
        self._results_map.clear()
        self._completed_tc_ids.clear()
        self._pending_tc_ids.clear()
        self._execution_depth = 0
        self._schedule_depth = 0
        self._schedule_lock = asyncio.Lock()

    def _cleanup_batch_records(self) -> None:
        """清除跨批累积记录（最外层 schedule() 返回后调用）

        清理由前序批次累积的 DAG 节点、结果映射和边界记录，
        防止跨轮次 DAG 膨胀和状态污染。

        安全前提：
        - 最外层 schedule() 已返回，无并发执行的工具
        - 所有 SubAgent 已完成，无嵌套调用
        - 无其他协程持有对旧 DAG 节点的引用
        """
        _logger.debug("ToolScheduler: 清除跨批记录")
        self._batch_boundaries.clear()
        self._results_map.clear()
        self._completed_tc_ids.clear()
        self._global_tool_calls.clear()
        self._global_dag = None
        self._prev_non_dispatch_ids.clear()

    @classmethod
    def reset_default(cls) -> None:
        """重置默认单例（仅用于测试清理）"""
        global _default_scheduler
        if _default_scheduler is not None:
            _default_scheduler._reset_global_state()
        _default_scheduler = None
        cls._semaphore = None

    async def _run_tool_func(self, func, tc, run_method) -> Tuple[str, bool]:
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
            func = self._registry.dispatch(tc["name"], tc["arguments"], agent=agent_ref)
            # 注入 agent_type（SubAgent 通过此属性限制 plan 的写入路径）
            if hasattr(agent_ref, 'agent_type') and agent_ref.agent_type is not None:
                func.agent_type = agent_ref.agent_type
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

    async def _execute_global_dag_async(
        self,
        dag,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
        current_batch_ids: set[str],
    ) -> List[Tuple[str, str, bool]]:
        """在全局 DAG 上执行，仅返回当前批次工具的结果。

        支持多批并发：
        - 层间循环中每执行完一层后重新拓扑排序 DAG
        - dispatch_agent 的 SubAgent 通过 add_batch 新增的节点
          被后续迭代自动捕获并执行
        - 批间串行通过 prev_non_dispatch_ids 依赖边保证

        Args:
            dag: ToolDAG 实例（全局 DAG）
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None  执行前回调
            on_after: (tc, output, success) -> None  执行后回调
            run_method: (func, tc) -> str  自定义执行方式
            current_batch_ids: 当前批次的 tc_id 集合，用于过滤返回结果

        Returns:
            当前批次的 [(tool_call_id, output, success)] 列表
        """
        if dag.size == 0:
            return []

        def _build_pending_calls(check_ids: set[str]) -> list[dict]:
            calls = []
            for tc_id in check_ids:
                node = dag.get_node(tc_id)
                if node is not None:
                    calls.append({
                        "id": node.tc_id,
                        "name": node.name,
                        "arguments": node.arguments,
                    })
            return calls

        if dag.has_cycle():
            _logger.warning("_execute_global_dag_async: DAG 存在环，并发执行剩余工具")
            pending = current_batch_ids - set(self._results_map.keys()) - self._pending_tc_ids
            if pending:
                pending_calls = _build_pending_calls(pending)
                if pending_calls:
                    results = await self._execute_concurrent(
                        pending_calls, agent_ref=agent_ref,
                        on_before=on_before, on_after=on_after,
                        run_method=run_method,
                    )
                    for r in results:
                        self._results_map[r[0]] = r
                        self._completed_tc_ids.add(r[0])
            return [self._results_map[tc_id] for tc_id in current_batch_ids
                    if tc_id in self._results_map]

        # ── 逐层执行 + 层间重拓扑（多批并发支持） ──
        # 设计：每次执行一层后重新拓扑排序 DAG。dispatch_agent 的 SubAgent
        # 通过 add_batch 新增的节点被后续迭代自动捕获并执行。
        # 这实现了 dispatch_agent 不阻塞下一批的语义。
        self._execution_depth += 1
        is_outermost = (self._execution_depth == 1)
        all_pending_ids: set[str] = set()

        try:
            max_iterations = 200  # 防死循环安全上限
            iteration = 0

            while iteration < max_iterations:
                iteration += 1

                # 每层完成后重新拓扑，捕获 dispatch_agent SubAgent 新增的节点
                layers = dag.topological_sort()
                if layers is None:
                    _logger.warning(
                        "_execute_global_dag_async: 拓扑排序失败，并发执行剩余工具"
                    )
                    pending = current_batch_ids - set(self._results_map.keys()) - self._pending_tc_ids
                    if pending:
                        pending_calls = _build_pending_calls(pending)
                        if pending_calls:
                            results = await self._execute_concurrent(
                                pending_calls, agent_ref=agent_ref,
                                on_before=on_before, on_after=on_after,
                                run_method=run_method,
                            )
                            for r in results:
                                self._results_map[r[0]] = r
                    break

                # 找到第一个含未执行节点的层
                # ★ 排除 _pending_tc_ids 中的节点（正在被外层调用执行），
                #   防止嵌套 _execute_global_dag_async 重复执行外层正在处理的节点
                target_layer: list[str] | None = None
                for layer in layers:
                    unexecuted = [
                        tc_id for tc_id in layer
                        if tc_id not in self._results_map
                        and tc_id not in self._pending_tc_ids
                    ]
                    if unexecuted:
                        target_layer = unexecuted
                        break

                if target_layer is None:
                    break  # 全部节点已执行

                # 最外层：将本层节点标记为 pending（嵌套保护）
                if is_outermost:
                    for tc_id in target_layer:
                        if tc_id not in all_pending_ids:
                            all_pending_ids.add(tc_id)
                            self._pending_tc_ids.add(tc_id)

                # 构建该层 tool_call 列表
                layer_calls = []
                for tc_id in target_layer:
                    node = dag.get_node(tc_id)
                    if node is not None:
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
                    self._results_map[r[0]] = r

                # 层执行完成 → 继续循环，重新拓扑捕获新增节点

        finally:
            self._execution_depth -= 1
            if is_outermost:
                for tc_id in all_pending_ids:
                    self._pending_tc_ids.discard(tc_id)

        # 标记当前批次中已完成的工具
        for tc_id in current_batch_ids:
            if tc_id in self._results_map:
                self._completed_tc_ids.add(tc_id)

        # 返回当前批次的结果
        return [self._results_map[tc_id] for tc_id in dag.original_order
                if tc_id in current_batch_ids and tc_id in self._results_map]

    async def schedule(
        self,
        tool_calls: list,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, str, bool]]:
        """统一调度入口：通过全局 DAG 进行多批拓扑调度（唯一工具执行路径）。

        - 空列表 → 返回 []
        - 所有工具（单工具/多工具）→ 全局 DAG 调度（累积工具到全局 DAG + 拓扑分层并发）

        多批并发策略：
        - 批间串行：上一批非 dispatch_agent 工具执行完之前，下一批等待
        - dispatch_agent 放行：上一批只剩 dispatch_agent 时，下一批可并行执行

        SubAgent 也使用全局 DAG：
        - 子 Agent 的工具调用被正确累积到全局 DAG
        - _prev_non_dispatch_ids 在外层 schedule() 返回前被外层批次覆盖
        - _results_map 确保已执行节点被跳过

        Args:
            tool_calls: 工具调用列表 [{"id", "name", "arguments"}]
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None  执行前回调
            on_after: (tc, output, success) -> None  执行后回调
            run_method: (func, tc) -> str  自定义执行方式

        Returns:
            [(tool_call_id, output, success)] 列表
        """
        # 提取 agent 来源标识（用于可观测性日志）
        agent_label = getattr(agent_ref, 'label', None) or type(agent_ref).__name__

        # ★ 跟踪 schedule 嵌套深度，防止 SubAgent 调用污染 _prev_non_dispatch_ids
        # SubAgent 通过 asyncio.gather 并发执行时，各自的 schedule() 调用会相互
        # 覆盖 _prev_non_dispatch_ids。只有最外层（MainAgent）的调用才应更新该字段。
        is_outermost_schedule = (self._schedule_depth == 0)
        self._schedule_depth += 1

        def _update_prev_ids():
            """仅在非 SubAgent 路径更新 _prev_non_dispatch_ids"""
            if is_outermost_schedule:
                self._prev_non_dispatch_ids = {
                    tc["id"] for tc in tool_calls
                    if tc.get("name") != "dispatch_agent"
                }

        try:
            if not tool_calls:
                _logger.debug("schedule[%s]: 空列表，返回 []", agent_label)
                return []

            # ── 全局 DAG 路径（单工具/多工具统一） ──────────────
            _logger.debug("schedule[%s]: %d 个工具 → 全局 DAG 调度",
                          agent_label, len(tool_calls))

            current_batch_ids = {tc["id"] for tc in tool_calls}

            try:
                # ── 临界区：DAG 创建/扩展 + _batch_boundaries 更新 ──
                # 锁保护 _global_dag / _global_tool_calls / _batch_boundaries
                # 以及 add_batch 中对 _prev_non_dispatch_ids 的读取。
                # 锁不覆盖 await 执行调用，避免嵌套 SubAgent schedule() 死锁。
                async with self._schedule_lock:
                    if self._global_dag is None:
                        # 首批：创建全局 DAG
                        _logger.debug("schedule[%s]: 首批 %d 个工具 → 创建全局 DAG",
                                      agent_label, len(tool_calls))
                        self._global_dag = ToolDAG(tool_calls, self._registry)
                        self._global_tool_calls = list(tool_calls)
                    else:
                        # 后续批：扩展全局 DAG
                        _logger.debug("schedule[%s]: 扩展批次 %d 个工具 → add_batch",
                                      agent_label, len(tool_calls))
                        self._global_dag.add_batch(
                            tool_calls, self._registry,
                            prev_non_dispatch_ids=self._prev_non_dispatch_ids,
                        )
                        self._global_tool_calls.extend(tool_calls)

                    self._batch_boundaries.append(len(self._global_tool_calls))

                # ── 锁外：执行全局 DAG（不持锁，避免嵌套死锁） ──
                results = await self._execute_global_dag_async(
                    self._global_dag,
                    agent_ref=agent_ref,
                    on_before=on_before,
                    on_after=on_after,
                    run_method=run_method,
                    current_batch_ids=current_batch_ids,
                )

                # ── 临界区：更新 _prev_non_dispatch_ids ──
                async with self._schedule_lock:
                    _update_prev_ids()
                return results

            except asyncio.CancelledError:
                # Python 3.9+: CancelledError 继承自 BaseException 而非 Exception，
                # 不会被 except Exception 捕获。此处单独处理以保证 _prev_non_dispatch_ids
                # 更新，防止 SubAgent 残留 ID 泄漏到下一批。
                async with self._schedule_lock:
                    _update_prev_ids()
                _logger.warning(
                    "schedule[%s]: 全局 DAG 调度被取消 (%d 个工具)",
                    agent_label, len(tool_calls),
                )
                raise

            except Exception:
                async with self._schedule_lock:
                    _update_prev_ids()
                _logger.error(
                    "schedule[%s]: 全局 DAG 调度失败 (%d 个工具)",
                    agent_label, len(tool_calls), exc_info=True,
                )
                raise

        finally:
            self._schedule_depth -= 1
            # ★ 最外层 schedule() 返回后清理跨批记录，防止 DAG 膨胀
            # 条件：is_outermost_schedule 确保只有最外层调用触发清理
            #       self._schedule_depth == 0 确认嵌套深度已归零
            #       tool_calls 非空跳过空列表路径（current_batch_ids 未定义）
            if is_outermost_schedule and self._schedule_depth == 0 and tool_calls:
                self._cleanup_batch_records()

    async def _execute_concurrent(
        self, tool_calls: list, *,
        agent_ref, on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, str, bool]]:
        """并发执行工具调用列表，使用类级 Semaphore 限流（0 表示无限制）+ FIRST_EXCEPTION 级联取消。"""
        sem = ToolScheduler._semaphore

        async def _run_one(tc):
            if sem is not None:
                async with sem:
                    return await self._execute_one_async(
                        tc, agent_ref=agent_ref,
                        on_before=on_before, on_after=on_after,
                        run_method=run_method,
                    )
            return await self._execute_one_async(
                tc, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after,
                run_method=run_method,
            )

        tasks = {asyncio.ensure_future(_run_one(tc)): tc for tc in tool_calls}
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
            tasks.clear()
            raise
        finally:
            # 兜底：确保没有任务残留（正常退出时 tasks 已为空，此分支仅在非取消异常时触发）
            if tasks:
                for task in list(tasks.keys()):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks.keys(), return_exceptions=True)

        # 按原顺序返回结果
        return [results_map[tc["id"]] for tc in tool_calls]
