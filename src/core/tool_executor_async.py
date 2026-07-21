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
- `schedule()` 为统一入口：空列表/单工具/多工具（含 ToolDAG 拓扑排序）
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
        self._schedule_depth: int = 0  # schedule() 嵌套深度（防止 SubAgent 污染 _prev_non_dispatch_ids）

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
                meta = self._registry.get_metadata(tc["name"])
                if meta is not None:
                    is_safe = meta.parallel_safe
            except Exception:
                _logger.debug("metadata 查询失败，工具 '%s' 默认串行执行", tc.get("name", "?"), exc_info=True)
                # 查询失败 → 默认串行（安全优先）

            if is_safe:
                parallel_calls.append(tc)
            else:
                serial_calls.append(tc)

        results_map: dict[str, tuple[str, str, bool]] = {}

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
            all_calls: list[dict] = []
            for tc_id in dag.original_order:
                node = dag.get_node(tc_id)
                if node is None:
                    _logger.warning("execute_dag_async (cycle fallback): get_node(%s) 返回 None，跳过", tc_id)
                    continue
                all_calls.append({
                    "id": tc_id,
                    "name": node.name,
                    "arguments": node.arguments,
                })
            return await self._execute_serial(
                all_calls, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after, run_method=run_method,
            )

        # 获取拓扑层
        layers = dag.topological_sort()
        if layers is None:
            # 拓扑排序失败（理论上 has_cycle 已检测，兜底回退串行）
            all_calls: list[dict] = []
            for tc_id in dag.original_order:
                node = dag.get_node(tc_id)
                if node is None:
                    _logger.warning("execute_dag_async (topo fallback): get_node(%s) 返回 None，跳过", tc_id)
                    continue
                all_calls.append({
                    "id": tc_id,
                    "name": node.name,
                    "arguments": node.arguments,
                })
            return await self._execute_serial(
                all_calls, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after, run_method=run_method,
            )

        # 逐层执行（提取为公共方法 _execute_layers）
        results_map: dict[str, tuple[str, str, bool]] = {}
        await self._execute_layers(
            dag, layers,
            agent_ref=agent_ref, on_before=on_before,
            on_after=on_after, run_method=run_method,
            results_map=results_map,
        )

        # 按原始顺序返回
        return [results_map[tc_id] for tc_id in dag.original_order
                if tc_id in results_map]

    async def _execute_layers(
        self,
        dag,
        layers: list[list[str]],
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
        results_map: dict[str, tuple[str, str, bool]],
        skip_tc_ids: set[str] | None = None,
    ) -> None:
        """逐层执行 DAG 拓扑层，将结果写入 results_map。

        跳过已在 results_map 中或 skip_tc_ids 中的 tc_id。
        同层工具并发执行（asyncio.gather + Semaphore 限流），层间串行。

        Args:
            dag: ToolDAG 实例
            layers: 拓扑排序后的层列表 [[tc_id, ...], ...]
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None  执行前回调
            on_after: (tc, output, success) -> None  执行后回调
            run_method: (func, tc) -> str  自定义执行方式
            results_map: 结果累加字典（可变），方法执行后包含本层结果
            skip_tc_ids: 要跳过的 tc_id 集合（如正在执行的 dispatch_agent）
        """
        for layer in layers:
            if not layer:
                continue

            # 过滤：跳过已有结果 / 已跳过节点
            layer_ids = [
                tc_id for tc_id in layer
                if tc_id not in results_map
                and (skip_tc_ids is None or tc_id not in skip_tc_ids)
            ]
            if not layer_ids:
                continue

            # 构建当前层的 tool_call dict 列表
            layer_calls = []
            for tc_id in layer_ids:
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

        dispatch_agent 也通过 _execute_layers 正常执行（在其所在层内并发执行），
        不拆分为后台任务，保证 dispatch_agent 的 _shared_executor 生命周期正确。

        有环时回退到串行。

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
            _logger.warning("_execute_global_dag_async: DAG 存在环，回退到串行")
            pending_calls = _build_pending_calls(current_batch_ids)
            if pending_calls:
                serial_results = await self._execute_serial(
                    pending_calls, agent_ref=agent_ref,
                    on_before=on_before, on_after=on_after,
                    run_method=run_method,
                )
                for r in serial_results:
                    self._results_map[r[0]] = r
                    self._completed_tc_ids.add(r[0])
            return [self._results_map[tc_id] for tc_id in current_batch_ids
                    if tc_id in self._results_map]

        layers = dag.topological_sort()
        if layers is None:
            _logger.warning("_execute_global_dag_async: 拓扑排序失败，回退到串行")
            pending_calls = _build_pending_calls(current_batch_ids)
            if pending_calls:
                serial_results = await self._execute_serial(
                    pending_calls, agent_ref=agent_ref,
                    on_before=on_before, on_after=on_after,
                    run_method=run_method,
                )
                for r in serial_results:
                    self._results_map[r[0]] = r
                    self._completed_tc_ids.add(r[0])
            return [self._results_map[tc_id] for tc_id in current_batch_ids
                    if tc_id in self._results_map]

        # ── 逐层执行（dispatch_agent 在层内正常并发执行） ──
        # 在开始执行前，将所有待执行节点标记为 pending，防止嵌套调用
        # （SubAgent 递归 schedule()）重复执行同一节点。
        self._execution_depth += 1
        is_outermost = (self._execution_depth == 1)

        if is_outermost:
            # 最外层：收集所有待执行节点标记为 pending
            all_layer_ids: set[str] = set()
            for layer in layers:
                for tc_id in layer:
                    if tc_id not in self._results_map:
                        all_layer_ids.add(tc_id)
                        self._pending_tc_ids.add(tc_id)

        try:
            # 外层执行所有层；嵌套调用跳过 _pending_tc_ids 中的节点
            await self._execute_layers(
                dag, layers,
                agent_ref=agent_ref, on_before=on_before,
                on_after=on_after, run_method=run_method,
                results_map=self._results_map,
                skip_tc_ids=self._pending_tc_ids if not is_outermost else None,
            )
        finally:
            self._execution_depth -= 1
            if is_outermost:
                for tc_id in all_layer_ids:
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
        """统一调度入口：通过全局 DAG 进行多批拓扑调度。

        - 空列表 → 返回 []
        - 单工具 → execute_async(parallel=False) 直接执行
        - 多工具 → 全局 DAG 调度（累积工具到全局 DAG + 拓扑分层并发）
          - DAG 构建失败时回退到全串行

        SubAgent 也使用全局 DAG，嵌套调用时与主 Agent 共享同一全局 DAG：
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

            if len(tool_calls) == 1:
                _logger.debug("schedule[%s]: 单工具 '%s' → 直接执行",
                              agent_label, tool_calls[0].get("name", "?"))
                result = await self.execute_async(
                    tool_calls, agent_ref=agent_ref,
                    on_before=on_before, on_after=on_after,
                    run_method=run_method, parallel=False,
                )
                # 单工具路径也需要更新 _prev_non_dispatch_ids，
                # 否则下一批 add_batch 会遗漏本批的非 dispatch_agent 工具
                _update_prev_ids()
                return result

            # ── 全局 DAG 路径 ───────────────────────────────────
            _logger.debug("schedule[%s]: %d 个工具 → 全局 DAG 调度",
                          agent_label, len(tool_calls))

            current_batch_ids = {tc["id"] for tc in tool_calls}

            try:
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

                # 执行全局 DAG，仅返回当前批次结果
                results = await self._execute_global_dag_async(
                    self._global_dag,
                    agent_ref=agent_ref,
                    on_before=on_before,
                    on_after=on_after,
                    run_method=run_method,
                    current_batch_ids=current_batch_ids,
                )

                _update_prev_ids()
                return results

            except asyncio.CancelledError:
                # Python 3.9+: CancelledError 继承自 BaseException 而非 Exception，
                # 不会被 except Exception 捕获。此处单独处理以保证 _prev_non_dispatch_ids
                # 更新，防止 SubAgent 残留 ID 泄漏到下一批。
                _update_prev_ids()
                _logger.warning(
                    "schedule[%s]: 全局 DAG 调度被取消 (%d 个工具)",
                    agent_label, len(tool_calls),
                )
                raise

            except Exception:
                _update_prev_ids()
                _logger.warning(
                    "schedule[%s]: 全局 DAG 调度失败，回退到全串行执行 (%d 个工具)",
                    agent_label, len(tool_calls), exc_info=True,
                )
                return await self._execute_serial(
                    tool_calls, agent_ref=agent_ref,
                    on_before=on_before, on_after=on_after,
                    run_method=run_method,
                )

        finally:
            self._schedule_depth -= 1

    async def _execute_serial(
        self, tool_calls: list, *,
        agent_ref, on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, str, bool]]:
        """串行执行工具调用列表，保持原始顺序，遇到取消则停止后续并 re-raise。"""
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
                raise
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
