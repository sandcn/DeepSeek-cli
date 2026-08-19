"""ToolScheduler — 全局工具调用调度器

统一调度入口，内聚 ToolDAG 构建 + 调度 + 并发控制，为 Main Agent 和所有 SubAgent
提供同一全局单例。

设计要点：
- 工具执行使用 asyncio 原生 async/await（无额外线程池）
- subagent 使用 asyncio.Event 纯异步等待，不消耗线程池工人
- 不支持超时（所有工具等待到底，避免误杀长时间任务）
- 支持 asyncio.gather 实现真正的并发
- Semaphore 为类级全局单例，跨 Agent 实例共享限流
- module-level `_default_scheduler` 单例 + `ToolScheduler.default()` 获取
- `schedule()` 为统一且唯一的入口：所有工具都走全局 DAG 路径
- 多批并发：批间串行执行，上一批只剩 subagent 时下一批可并行
- SubAgent 工具调用同样走全局 DAG 路径
"""

# UNIQUE_PATH: 以下两处是项目中仅有的调用方，无其他工具执行路径
#   - _tool_callbacks.py:96（MainAgent）
#   - subagent.py:300（SubAgent）

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, List, Tuple, Optional, Callable

from ..tools.registry import ToolRegistry
from ..tools.base import ToolResult
from .param_formatter import extract_key_params
from .tool_dag import ToolDAG

_logger = logging.getLogger(__name__)

# ------------------------------------------------------------
#  全局单例引用（懒初始化）
# ------------------------------------------------------------
_default_scheduler: Optional[ToolScheduler] = None

_MAX_CONCURRENT_TOOLS = 0  # 最大并发工具数，0 表示无限制
_MAX_DAG_ITERATIONS = 200    # DAG while 循环最大迭代次数（防死循环）
_BASH_POLL_INTERVAL = 0.1   # bash 运行中无 subagent 时的轮询间隔（秒）


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
        self._running_bash_ids: set[str] = set()
        self._background_dispatch_tasks: list[asyncio.Task] = []
        self._execution_depth: int = 0  # _execute_global_dag_async 嵌套深度
        # ── 双重保护机制（分工协作） ─────────────────────
        # _schedule_lock：asyncio.Lock 防并发竞态 — 多 SubAgent 通过
        #   asyncio.gather 并发调用 schedule() 时保护共享状态修改。
        # _schedule_depth：int 防语义污染 — SubAgent 嵌套调用时只有
        #   最外层 MainAgent 的调用才更新 _prev_non_dispatch_ids。
        self._schedule_depth: int = 0
        # ★ 2026-08-20（性能/稳定性修复）：_schedule_lock 惰性创建——asyncio.Lock()
        #   构造依赖当前事件循环（Python 3.9 get_event_loop），无运行循环时构造
        #   抛 RuntimeError（asyncio.run 之后的同进程内）。None + 首次 async
        #   使用创建，构造调度器不再绑定循环。
        self._schedule_lock: Optional[asyncio.Lock] = None

    def _get_schedule_lock(self) -> asyncio.Lock:
        """惰性创建调度锁（仅 async 上下文调用，保证存在运行事件循环）。"""
        if self._schedule_lock is None:
            self._schedule_lock = asyncio.Lock()
        return self._schedule_lock

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
        self._running_bash_ids.clear()
        self._background_dispatch_tasks.clear()
        self._execution_depth = 0
        self._schedule_depth = 0
        self._schedule_lock = None

    async def _cleanup_batch_records(self) -> None:
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
        self._pending_tc_ids.clear()
        self._running_bash_ids.clear()
        # ★ 不再取消仍在执行的后台 subagent 任务（P0-1 修复）：
        #   subagent 提前返回（_handle_subagent_early_return）后，
        #   bg 任务可能在等待 SubAgent 完成（ParallelExecutor barrier / gather）。
        #   清理入口若在此 cancel，SubAgent 被取消、结果丢失——与用户报告的
        #   "多个 SubAgent 并发执行时偶发失败"高度相关。
        #   仅移除已完成任务（其 _on_bg_subagent_done 回调已执行/由本行清除）；
        #   未完成的任务保留，由 schedule() finally 在确认全部完成后统一清理。
        #   ⚠ 过滤方向：保留未完成（not t.done()），移除已完成——否则本方法在
        #   全部 bg done 后调用时列表全量保留、永久累积（内存泄漏）。
        #
        #   生命周期设计（review P3）：未完成 bg 任务在 SubAgent 运行期间
        #   必须保留（其结果需补发到 agent 消息保证消息序列完整）；SubAgent
        #   自然结束（模型调用无超时、等待到底）后任务完成，由下一次
        #   _cleanup_batch_records 移除——滞留时长与 SubAgent 执行时长一致，
        #   非泄漏。仅当 SubAgent 永久挂起（极端网络场景）时任务长期保留，
        #   属"等待到底"设计语义，不做强制回收。
        self._background_dispatch_tasks = [
            t for t in self._background_dispatch_tasks if not t.done()
        ]
        self._prev_non_dispatch_ids.clear()

    def _find_next_layer(self, dag, layers, is_outermost: bool = True) -> list[str] | None:
        """在拓扑排序的层次中查找首个包含未执行节点的层。

        同时应用 bash 独占过滤：若 bash 工具正在运行，仅允许 subagent 通过。

        Args:
            dag: ToolDAG 实例
            layers: 拓扑排序后的层次列表 [[tc_id1, tc_id2], ...]
            is_outermost: 是否为最外层 schedule() 调用。
                ★ 关键修复（2026-08-06）：bash 独占过滤**仅对最外层调度生效**。
                修复前 ``_running_bash_ids`` 为全局单例状态，主 Agent 的 bash
                运行期间（如长时编译）会跨 SubAgent 上下文拦截**所有子代理的
                工具调用**——子代理工具被 ``bash 独占过滤`` 拦成空层后无限轮询
                等待 bash 完成，若 bash 卡住（进程树清理不彻底等），子代理永远
                卡在工具 parsing 状态（用户侧现象：子代理「接收参数后不执行」）。
                嵌套调用（SubAgent，is_outermost=False）跳过独占过滤，保证
                子代理工具可独立调度，不被父 Agent 的 bash 阻塞。

        Returns:
            None: 所有节点均已执行
            list[str]: 当前应执行的 tc_id 列表
            []: bash 独占过滤后为空（调用方应进行 bash 轮询等待）
        """
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
            return None  # 全部节点已执行

        # ── bash 独占运行：bash 运行中仅 subagent 可并行 ──
        # 若已有 bash 工具正在运行，当前层仅允许 subagent 通过，
        # 其他工具（read/write/bash/interactive）须等待 bash 完成。
        # ★ 仅最外层调度应用（SubAgent 嵌套调用跳过——见 docstring）。
        if is_outermost and self._running_bash_ids:
            filtered = []
            for tc_id in target_layer:
                node = dag.get_node(tc_id)
                if node is not None:
                    if (node.tool_category == "general"
                            and node.name == "subagent"):
                        filtered.append(tc_id)
            target_layer = filtered
            if not target_layer:
                return []  # bash 独占过滤为空，调用方应轮询等待

        return target_layer

    def _only_subagent_remaining(self, dag: ToolDAG) -> list[dict[str, Any]] | None:
        """检查 DAG 中所有未执行节点是否全部为 subagent。

        遍历 DAG 中所有节点，过滤出不在 _results_map 且不在 _pending_tc_ids
        中的节点。若全部为 subagent（tool_category="general"
        且 name="subagent"），返回剩余 subagent 列表；
        否则返回 None。

        用于多批并发的提前返回检测：当仅剩 subagent 时，
        可将其作为后台任务执行，不阻塞外层 schedule() 返回。
        """
        if not dag.nodes:
            return None  # 空 DAG：无节点需要执行，不触发提前返回
        remaining = []
        for node in dag.nodes.values():
            tc_id = node.tc_id
            if tc_id not in self._results_map and tc_id not in self._pending_tc_ids:
                if not (node.tool_category == "general" and node.name == "subagent"):
                    return None
                remaining.append({
                    "id": node.tc_id,
                    "name": node.name,
                    "arguments": node.arguments,
                })
        if not remaining:
            return None
        return remaining

    async def _execute_one_layer(
        self,
        dag,
        target_layer: list[str],
        is_outermost: bool,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> None:
        """执行 DAG 中的一层工具调用。

        构建该层 tool_call 列表 → 标记 bash 运行状态 → 并发执行 → 清理 bash 标记。

        Args:
            dag: ToolDAG 实例
            target_layer: 当前层 tc_id 列表
            is_outermost: 是否为最外层调用
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None 执行前回调
            on_after: (tc, output, success) -> None 执行后回调
            run_method: (func, tc) -> str 自定义执行方式

        Returns:
            None（通过 self._results_map 副作用记录结果）
        """
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
            return

        # ── 标记本层 bash 工具进入运行状态 ──
        bash_ids_in_layer: set[str] = set()
        if is_outermost:
            for tc_id in target_layer:
                node = dag.get_node(tc_id)
                if node is not None and node.tool_category == "bash":
                    bash_ids_in_layer.add(tc_id)
                    self._running_bash_ids.add(tc_id)

        try:
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
        finally:
            # ── 层执行完成（含异常路径），清除本层 bash 运行标记 ──
            if is_outermost and bash_ids_in_layer:
                for bash_id in bash_ids_in_layer:
                    self._running_bash_ids.discard(bash_id)

    def _handle_subagent_early_return(
        self,
        dag,
        is_outermost: bool,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> bool:
        """检测 DAG 中所有未执行节点是否均为 subagent。

        若是，将其作为后台任务异步执行并提前返回，不阻塞外层 schedule() 返回。
        这实现了 subagent 提前放行的语义。

        Args:
            dag: ToolDAG 实例
            is_outermost: 是否为最外层调用
            agent_ref: 传给 registry.dispatch() 的 agent 引用
            on_before: (tc, detail) -> None 执行前回调
            on_after: (tc, output, success) -> None 执行后回调
            run_method: (func, tc) -> str 自定义执行方式

        Returns:
            True: 已触发提前返回（调用方应 break while 循环）
            False: 不触发提前返回
        """
        remaining_dispatch = self._only_subagent_remaining(dag)
        if not is_outermost or remaining_dispatch is None:
            return False

        # 标记为 pending，防止嵌套 _execute_global_dag_async 重复处理
        for tc in remaining_dispatch:
            self._pending_tc_ids.add(tc["id"])

        # 创建后台协程：复用 _execute_concurrent 执行 subagent
        bg_task = asyncio.ensure_future(
            self._bg_subagents(
                remaining_dispatch, agent_ref=agent_ref,
                on_before=on_before, on_after=on_after,
                run_method=run_method,
            )
        )

        # 异常兜底回调：后台 Task 异常/取消时写入失败结果
        bg_task.add_done_callback(
            lambda task, rd=remaining_dispatch, ar=agent_ref:
                self._on_bg_subagent_done(task, rd, ar)
        )
        self._background_dispatch_tasks.append(bg_task)
        return True  # 退出 while 循环，提前返回

    async def _bg_subagents(
        self,
        remaining_dispatch: list[dict[str, Any]],
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> None:
        """后台执行剩余的 subagent 工具列表。

        复用 _execute_concurrent 执行 subagent，完成后将结果写入
        _results_map 和 _completed_tc_ids。此方法由 asyncio.ensure_future
        调度为后台 Task，不阻塞外层 schedule() 返回。

        ★ P3 修复（2026-08-08）：提前返回路径下 subagent 的工具结果
          不会经 schedule() 返回给调用方（schedule() 在 bg 任务完成前已返回，
          batch_results 仅含非 subagent 节点）。若不在对话中补发 tool result，
          下一轮模型调用的消息序列缺 tool 消息 → API 报错或模型重发。
          这里在结果写入 _results_map 的同时，直接补发到 agent 消息。
        """
        results = await self._execute_concurrent(
            remaining_dispatch, agent_ref=agent_ref,
            on_before=on_before, on_after=on_after,
            run_method=run_method,
        )
        for r in results:
            self._results_map[r[0]] = r
            self._completed_tc_ids.add(r[0])
            # 补发 tool result 到对话（仅提前返回路径调用本方法，不会重复）
            try:
                if hasattr(agent_ref, '_append_tool_result'):
                    agent_ref._append_tool_result(r[0], r[1])
            except Exception:
                _logger.debug("补发 subagent tool result 失败", exc_info=True)

    def _append_bg_failure_result(
        self,
        tc_id: str,
        message: str,
        agent_ref: Any,
    ) -> None:
        """写入并补发一个失败的后台 subagent 结果（P2-1 提取）。

        供 _on_bg_subagent_done 的取消/异常兜底路径复用：
        - 写入 _results_map / _completed_tc_ids（调度器内部结果一致）；
        - 补发 tool result 到 agent 消息（保证下一轮模型调用消息序列完整）。
        """
        if tc_id in self._results_map:
            return  # 已有结果，不重复写入/补发
        self._results_map[tc_id] = (tc_id, message, False)
        self._completed_tc_ids.add(tc_id)
        try:
            if agent_ref is not None and hasattr(agent_ref, '_append_tool_result'):
                agent_ref._append_tool_result(tc_id, message)
        except Exception:
            _logger.debug("补发 subagent 失败 tool result 异常", exc_info=True)

    def _on_bg_subagent_done(
        self,
        task: asyncio.Task,
        remaining_dispatch: list[dict[str, Any]],
        agent_ref: Any = None,
    ) -> None:
        """后台 subagent 任务完成回调（含异常兜底）。

        防御性检查：若 _global_dag 已为 None（表示 _cleanup_batch_records 已清理），
        直接返回，避免操作已清空的状态。Task 已完成即从 _background_dispatch_tasks
        移除自身（防止列表累积——_cleanup_batch_records 仅保留未完成任务）。
        """
        # 防御：_cleanup_batch_records 已清理 → 不再操作
        if self._global_dag is None:
            return

        # 主动从后台任务列表移除自身（done task 不应长期留在列表中）
        try:
            if task in self._background_dispatch_tasks:
                self._background_dispatch_tasks.remove(task)
        except ValueError:
            pass

        try:
            if task.cancelled():
                # ★ 取消路径补发失败结果（P3）：bg 任务被取消时同样保证
                #   消息序列完整（tool_call 有对应 tool 消息）。当前无取消源
                #   （_cleanup_batch_records 已不再取消 bg 任务），此分支为
                #   防御性兜底（未来用户中断等场景）。
                for tc in remaining_dispatch:
                    self._append_bg_failure_result(
                        tc["id"], "后台 subagent 已被取消", agent_ref,
                    )
                return  # 已被取消，_pending_tc_ids 由 finally 统一清理
            exc = task.exception()
            if exc is not None:
                # 防御性死代码（P2-1 注释）：_execute_concurrent 将工具失败转为
                # 结果元组不抛异常；唯一非正常路径是 CancelledError（已由上面
                # cancelled 分支处理）。保留本分支作为未来异常来源的兜底。
                for tc in remaining_dispatch:
                    self._append_bg_failure_result(
                        tc["id"],
                        f"后台 subagent 执行失败: {exc}",
                        agent_ref,
                    )
        finally:
            for tc in remaining_dispatch:
                self._pending_tc_ids.discard(tc["id"])

    @classmethod
    def reset_default(cls) -> None:
        """重置默认单例（仅用于测试清理）"""
        global _default_scheduler
        if _default_scheduler is not None:
            _default_scheduler._reset_global_state()
        _default_scheduler = None
        cls._semaphore = None

    async def wait_background_dispatch(self, timeout: float | None = 180.0) -> None:
        """等待所有后台 subagent 任务完成（供调用方同步消息序列）。

        subagent 提前返回（_handle_subagent_early_return）后，
        剩余 dispatch 在后台任务中执行，其 tool result 由 _bg_subagents
        补发到 agent 消息。调用方（MainAgent handle_tool_calls）在继续下一轮
        模型调用前等待这些任务完成，确保消息序列完整——否则下一轮模型请求
        携带「有 tool_calls 但无对应 tool 消息」的历史消息，部分 provider
        会返回 400 错误或导致模型重发。

        超时语义（P3-1 修复）：超时后**不取消**未完成的 bg 任务——其 SubAgent
        可能仍在执行（模型调用无超时、后台等待有界），取消会杀死用户等待的
        子代理结果。改为放弃等待并返回，bg 任务继续运行、完成后正常补发结果
        （与 _process_background_tasks 的超时降级语义一致）。

        Args:
            timeout: 最长等待秒数（None 表示无限等待）。默认 180s，
                覆盖 SubAgent 后台等待超时（_BACKGROUND_WAIT_TIMEOUT=120s）上限。
        """
        pending = [t for t in self._background_dispatch_tasks if not t.done()]
        if not pending:
            return
        if timeout is None:
            await asyncio.gather(*pending, return_exceptions=True)
            return
        # 手写轮询：仅观察不干预（asyncio.wait 超时不 cancel 任务）
        deadline = time.monotonic() + timeout
        while True:
            pending = [t for t in pending if not t.done()]
            if not pending:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _logger.warning(
                    "等待后台 dispatch 任务超时（%d 个未完成），放弃等待（任务继续运行）",
                    len(pending),
                )
                return
            try:
                await asyncio.wait(pending, timeout=min(0.5, remaining))
            except asyncio.CancelledError:
                raise

    async def _run_tool_func(self, func, tc, run_method) -> Tuple[Any, bool]:
        """统一执行工具调用，处理 run_method 和直接执行两种路径

        若工具设置了 ``func.result_blocks``（多模态结构化结果），
        将文本返回包装为 ``ToolResult``（text + blocks），供消息链路
        转为多模态 content blocks；未设置时行为不变（返回 str）。

        Returns:
            (output, success) 元组；output 为 str 或 ToolResult。
        """
        if run_method:
            coro = run_method(func, tc)
        else:
            coro = func.execute()
        result = await coro
        if run_method:
            if isinstance(result, tuple):
                # 防御：run_method 可能返回长度不足的 tuple（异常实现），
                # 仅取首元素为文本，success 缺省视为成功
                if len(result) >= 2:
                    text, success = result[0], result[1]
                else:
                    text, success = result[0], True
            else:
                text, success = result, True
        else:
            text, success = result, True
        # 多模态结构化结果包装：execute() 返回文本 + result_blocks 提供 blocks
        blocks = getattr(func, "result_blocks", None)
        if blocks:
            if not isinstance(text, str):
                text = str(text or "")
            return ToolResult(text=text, blocks=blocks), success
        return text, success

    async def _execute_one_async(
        self,
        tc: dict,
        *,
        agent_ref,
        on_before: Optional[Callable],
        on_after: Optional[Callable],
        run_method: Optional[Callable],
    ) -> Tuple[str, Any, bool]:
        """异步执行单个工具调用，无超时限制，等待到底。

        所有工具（含 subagent）统一使用 async/await 路径，
        subagent 内部使用 asyncio.Event 纯异步等待 barrier，
        不消耗任何线程池工人。

        Returns:
            (tool_call_id, output, success)；output 为 str 或 ToolResult
            （多模态结构化结果，见 _run_tool_func）。
        """
        # 对齐 Claude Code：工具卡 detail 用关键参数**值**（非 JSON）——已知工具
        # 显示如 `Read pyproject.toml` 的路径/命令，未知工具显示紧凑 `k=v`
        try:
            detail = extract_key_params(tc["name"], tc["arguments"])
            if on_before:
                on_before(tc, detail)
        except Exception:
            # on_before（审计日志/参数摘要/display）异常不应让工具执行失败：
            # 仅记录日志，工具照常执行（避免模型收到虚假失败结果浪费一轮）
            _logger.warning(
                "工具 %s on_before 回调异常（忽略，继续执行）: %s",
                tc.get("name", "?"), exc_info=True,
            )

        try:
            func = self._registry.dispatch(tc["name"], tc["arguments"], agent=agent_ref)
            # 注入 agent_type（SubAgent 通过此属性限制 plan 的写入路径）
            if hasattr(agent_ref, 'agent_type') and agent_ref.agent_type is not None:
                func.agent_type = agent_ref.agent_type
            output, success = await self._run_tool_func(func, tc, run_method)
            if on_after:
                try:
                    on_after(tc, output, success)
                except Exception:
                    # on_after（展示/统计）异常不应改变工具结果
                    _logger.warning(
                        "工具 %s on_after 回调异常（忽略）: %s",
                        tc.get("name", "?"), exc_info=True,
                    )
            return (tc["id"], output, success)

        except asyncio.CancelledError:
            output = f"工具执行被取消: {tc['name']}"
            _logger.warning("Async tool %s cancelled", tc["name"])
            if on_after:
                try:
                    on_after(tc, output, False)
                except Exception:
                    _logger.warning("工具 %s on_after 回调异常（忽略）: %s",
                                    tc.get("name", "?"), exc_info=True)
            raise

        except Exception as e:
            output = f"工具执行失败: {e}"
            _logger.error("Async tool %s failed: %s", tc["name"], e, exc_info=True)

            if on_after:
                try:
                    on_after(tc, output, False)
                except Exception:
                    _logger.warning("工具 %s on_after 回调异常（忽略）: %s",
                                    tc.get("name", "?"), exc_info=True)

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
    ) -> List[Tuple[str, Any, bool]]:
        """在全局 DAG 上执行，仅返回当前批次工具的结果。

        支持多批并发：
        - 层间循环中每执行完一层后重新拓扑排序 DAG
        - subagent 的 SubAgent 通过 add_batch 新增的节点
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
        # 设计：每次执行一层后重新拓扑排序 DAG。subagent 的 SubAgent
        # 通过 add_batch 新增的节点被后续迭代自动捕获并执行。
        # 这实现了 subagent 不阻塞下一批的语义。
        self._execution_depth += 1
        is_outermost = (self._execution_depth == 1)
        all_pending_ids: set[str] = set()

        try:
            max_iterations = _MAX_DAG_ITERATIONS  # 防死循环安全上限
            iteration = 0

            while iteration < max_iterations:
                iteration += 1

                # 每层完成后重新拓扑，捕获 subagent SubAgent 新增的节点
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

                # 查找首个未执行节点层 + bash 独占过滤
                # ★ 传递 is_outermost：bash 独占过滤仅对最外层调度生效，
                #   SubAgent 嵌套调用（is_outermost=False）跳过独占过滤，
                #   不被父 Agent 的 bash 阻塞（修复子代理工具卡在 parsing）。
                target_layer = self._find_next_layer(
                    dag, layers, is_outermost=is_outermost,
                )
                if target_layer is None:
                    break  # 全部节点已执行

                if not target_layer:
                    # bash 运行中且无 subagent：让出控制权等待 bash 完成
                    await asyncio.sleep(_BASH_POLL_INTERVAL)
                    iteration -= 1  # bash 轮询不计入 _MAX_DAG_ITERATIONS
                    continue

                # ★ 将本层节点标记为 pending（防并发重复执行）
                #   P0-2 修复：修复前仅最外层（is_outermost=True）标记 pending，
                #   多个并发 SubAgent 的 _execute_global_dag_async（各自
                #   is_outermost=False）在 _find_next_layer 中可能同时选中同一
                #   层节点（_results_map/_pending_tc_ids 均未命中）→ 工具被
                #   重复执行（write_file 写两次、bash 跑两次——用户报告"多个
                #   SubAgent 并发大量 write_file/bash 偶发异常"的元凶）。
                #   改为无论是否最外层，选中节点一律标记 pending，由 finally
                #   统一 discard，消除选中竞态。
                for tc_id in target_layer:
                    if tc_id not in all_pending_ids:
                        all_pending_ids.add(tc_id)
                        self._pending_tc_ids.add(tc_id)

                # 执行本层工具（构建 layer_calls → 标记 bash → 执行 → 清理 bash 标记）
                await self._execute_one_layer(
                    dag, target_layer, is_outermost,
                    agent_ref=agent_ref,
                    on_before=on_before,
                    on_after=on_after,
                    run_method=run_method,
                )

                # 多批并发：仅剩 subagent 时提前返回
                if self._handle_subagent_early_return(
                    dag, is_outermost,
                    agent_ref=agent_ref,
                    on_before=on_before,
                    on_after=on_after,
                    run_method=run_method,
                ):
                    break  # subagent 已转为后台任务，提前返回

        finally:
            self._execution_depth -= 1
            # 所有层均标记 pending（P0-2 修复），finally 统一清理。
            # 注意：bg 任务通过 _on_bg_subagent_done 注入的 _pending_tc_ids
            # 条目由 _on_bg_subagent_done 的 finally 块负责清理，而非由此处
            # 的 all_pending_ids 遍历处理。
            for tc_id in all_pending_ids:
                self._pending_tc_ids.discard(tc_id)

        # 标记当前批次中已完成的工具
        for tc_id in current_batch_ids:
            if tc_id in self._results_map:
                self._completed_tc_ids.add(tc_id)

        # ★ 先捕获当前批次结果，再清理节点。
        #    若先 remove_nodes 再遍历 dag.original_order，已完成的
        #    节点 ID 已从 _original_order 中移除，导致结果被错误过滤为空列表。
        batch_results = [self._results_map[tc_id] for tc_id in current_batch_ids
                         if tc_id in self._results_map]

        # ── 清理已完成节点（方案A） ──
        # 当前批次中已执行完成的节点从 DAG 中移除，减少下一批的检测扫描量。
        # 已完成的节点结果已写入 _results_map，后续 add_batch 的 prev_non_dispatch_ids
        # 中若引用已删除节点，则 skip（语义等价——已完成节点不再需要等待）。
        # 注意：仅清理当前批次的节点，不清理其他批次（prev_non_dispatch_ids
        # 仅引用上一批的非 dispatch ID，非当前批）。
        if is_outermost and self._global_dag is not None:
            batch_completed = {tc_id for tc_id in current_batch_ids
                               if tc_id in self._completed_tc_ids}
            if batch_completed:
                self._global_dag.remove_nodes(batch_completed)

        return batch_results

    # 调用方：_tool_callbacks.py:96（MainAgent）、subagent.py:300（SubAgent）
    async def schedule(
        self,
        tool_calls: list,
        *,
        agent_ref,
        on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, Any, bool]]:
        """统一调度入口：通过全局 DAG 进行多批拓扑调度。

        本方法是项目中唯一的工具执行路径，所有工具调用（MainAgent + SubAgent）
        均通过此入口。

        - 空列表 → 返回 []
        - 所有工具（单工具/多工具）→ 全局 DAG 调度（累积工具到全局 DAG + 拓扑分层并发）

        多批并发策略：
        - 批间串行：上一批非 subagent 工具执行完之前，下一批等待
        - subagent 放行：上一批只剩 subagent 时，下一批可并行执行

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
                    if tc.get("name") != "subagent"
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
                # ── 临界区：最小化锁保护（P0-4: add_batch 移出锁外） ──
                # 锁内仅做：首批创建 DAG / 后续批捕获 add_batch 所需参数
                # add_batch 调用在锁外执行，消除嵌套 SubAgent schedule() 死锁风险
                async with self._get_schedule_lock():
                    if self._global_dag is None:
                        # 首批：在锁内创建 DAG（轻量操作，无 await，无死锁风险）
                        _logger.debug("schedule[%s]: 首批 %d 个工具 → 创建全局 DAG",
                                      agent_label, len(tool_calls))
                        self._global_dag = ToolDAG(tool_calls, self._registry)
                        self._global_tool_calls = list(tool_calls)
                        self._batch_boundaries.append(len(self._global_tool_calls))
                        _add_batch_needed = False
                    else:
                        # 后续批：锁内仅读取 add_batch 所需参数
                        _prev_ids_capture = set(self._prev_non_dispatch_ids)
                        _add_batch_needed = True

                # 锁外：add_batch（不持锁，消除嵌套死锁风险）
                if _add_batch_needed:
                    self._global_dag.add_batch(
                        tool_calls, self._registry,
                        prev_non_dispatch_ids=_prev_ids_capture,
                    )
                    self._global_tool_calls.extend(tool_calls)
                    # _batch_boundaries 在锁外更新：仅用于边界记录，不参与调度决策
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
                async with self._get_schedule_lock():
                    _update_prev_ids()
                return results

            except asyncio.CancelledError:
                # Python 3.9+: CancelledError 继承自 BaseException 而非 Exception，
                # 不会被 except Exception 捕获。此处单独处理以保证 _prev_non_dispatch_ids
                # 更新，防止 SubAgent 残留 ID 泄漏到下一批。
                async with self._get_schedule_lock():
                    _update_prev_ids()
                _logger.warning(
                    "schedule[%s]: 全局 DAG 调度被取消 (%d 个工具)",
                    agent_label, len(tool_calls),
                )
                raise

            except Exception:
                async with self._get_schedule_lock():
                    _update_prev_ids()
                _logger.error(
                    "schedule[%s]: 全局 DAG 调度失败 (%d 个工具)",
                    agent_label, len(tool_calls), exc_info=True,
                )
                raise

        finally:
            self._schedule_depth -= 1
            # ★ 清理条件：所有 schedule 调用（MainAgent + 并行 SubAgent）全部
            #   完成后才清理跨批记录，防止 DAG 膨胀 + 防止提前清理。
            #
            #   Bug 修复（2026-08-08）：修复前条件为
            #   ``is_outermost_schedule and self._schedule_depth == 0``——
            #   MainAgent dispatch 批次提前返回（subagent 转后台）时，
            #   SubAgent 可能仍在全局 DAG 上执行工具（_schedule_depth > 0），
            #   MainAgent 的 finally 却把深度减到 0 并立即清理全局状态
            #   （_results_map/_global_dag/_pending_tc_ids 等），导致并发
            #   SubAgent 的工具结果丢失/调度错乱（用户侧现象：多个 SubAgent
            #   并发写文件/跑 bash 时偶发卡死或重复执行）。
            #   改为仅当 _schedule_depth == 0（无任何活跃 schedule 调用）时清理；
            #   且存在未完成的后台 dispatch 任务时延迟清理（其 SubAgent 可能
            #   仍在运行，清理会取消 bg 任务 / 清空 SubAgent 依赖的全局状态）。
            #
            #   is_outermost_schedule 仍用于 _update_prev_ids 的"仅最外层更新
            #   _prev_non_dispatch_ids"语义，不与清理条件耦合。
            if self._schedule_depth == 0 and tool_calls:
                if any(not t.done() for t in self._background_dispatch_tasks):
                    _logger.debug(
                        "ToolScheduler: 有未完成的后台 dispatch 任务，延迟清理"
                    )
                else:
                    await self._cleanup_batch_records()

    async def _execute_concurrent(
        self, tool_calls: list, *,
        agent_ref, on_before: Optional[Callable] = None,
        on_after: Optional[Callable] = None,
        run_method: Optional[Callable] = None,
    ) -> List[Tuple[str, Any, bool]]:
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
                        tc2 = tasks.get(pt)
                        if tc2 is None:
                            continue
                        try:
                            # task may complete before cancel takes effect
                            result = await pt
                            results_map[tc2["id"]] = result
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
            remaining_tasks = list(tasks.keys())
            if remaining_tasks:
                for task in remaining_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*remaining_tasks, return_exceptions=True)
            tasks.clear()  # P0-2: 确保 tasks 引用在所有路径上被清除，消除 Task 残留引用

        # 按原顺序返回结果（防御极端时序下 results_map 缺失：asyncio.wait
        # 异常路径等，用失败占位保证调用方消息序列完整）
        return [
            results_map.get(
                tc["id"],
                (tc["id"], f"工具执行失败: 调度器未返回该工具调用的结果 ({tc['id']})", False),
            )
            for tc in tool_calls
        ]
