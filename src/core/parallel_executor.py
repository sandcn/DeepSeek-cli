"""
ParallelExecutor — 使用 asyncio.gather 并行调度多个 SubAgent

支持两种模式：
1. 独立模式：run() 直接创建并执行 agents
2. 批量模式：多个 dispatch_agent 调用共享同一实例，
   通过 add_agent() 注册，barrier 协调，execute_all() 统一执行
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import List, Dict, Any

from ._terminal import get_terminal_width as _get_terminal_width
from .internal.agent._capture_manager import _safe_restore as safe_restore_stdout
from .internal.agent._subagent_spawner import SubAgentSpawner
from .subagent import SubAgent

_logger = logging.getLogger(__name__)


def _publish_output(text: str, level: str = "info", source: str = "") -> None:
    """发布输出事件（经 get_output_publisher 工厂，工厂返回 None 时静默降级；真实无头模式经 OutputConsumer 兜底直写终端）。

    替代模块级 ``from ..tui.events.consumers import publish_output`` 的直接依赖，
    实现 core → display_target Protocol 依赖倒置。
    """
    from .display_target import get_output_publisher
    publisher = get_output_publisher()
    if publisher is not None:
        publisher(text, level=level, source=source)

# ── 常量 ──────────────────────────────────────────────
_TIMEOUT = 3.0  # 显示停止等超时（秒）
# barrier 兜底超时 — dispatch 等待为分钟级任务，60s 兜底防永久阻塞
# （任一 dispatch_agent 在 register_and_wait 前抛异常时，其余等待者
#  不会永久阻塞；超时后由 register_and_wait 主动触发 _execute_all 唤醒）
_BARRIER_TIMEOUT = 60.0

# ── 结果字典键常量 ─────────────────────────────────
_DESCRIPTION_KEY = "description"
_ERROR_KEY = "error"
_LABEL_KEY = "label"
_RESULT_KEY = "result"
_AGENT_TYPE_KEY = "agent_type"


# ── 终端尺寸查询 ─────────────────────────────────────
# 共享 get_terminal_width（src/core/_terminal.py）：优先 /dev/tty ioctl 查询，
# 不能依赖 shutil.get_terminal_size()（Android Termux 上返回陈旧环境变量值）。
# 保留 _get_terminal_width 兼容别名（步骤 1.4 迁移），避免外部调用点破坏。


class ParallelExecutor:
    """并行执行多个 SubAgent，统一管理显示

    支持批量模式：多个 dispatch_agent 调用共享同一实例，
    通过 add_agent() 注册 agent specs，register_and_wait() 协调全部注册完成后
    由 _execute_all() 统一创建 SubAgent 并并发执行。

    同步机制：
    - 使用 asyncio.Event（纯异步等待，不消耗线程池工人）
    - 最后一个注册的协程触发执行
    """

    def __init__(self, parent_agent, max_history: int = 3, agent_factory=None,
                 is_web: bool = False, config_port=None):
        self.parent = parent_agent
        self.max_history = max_history
        self._agent_factory = agent_factory or SubAgent
        self._is_web = is_web
        self._config_port = config_port

        # SubAgent 创建 + 显示委托
        self._spawner = SubAgentSpawner(parent_agent, self._agent_factory, is_web)

        # 批量模式状态
        self._pending_specs: List[Dict[str, Any]] = []
        self._results: List[Dict[str, Any]] = []
        self._expected_count = 0
        self._registered_count = 0
        self._agents_lock = asyncio.Lock()
        self._all_done = asyncio.Event()
        self._executing = False  # _execute_all 正在执行标志（防止超时重复触发）

    # -- 批量模式 API --

    @property
    def is_batch_mode(self) -> bool:
        """是否已设置为批量模式（用于 dispatch_agent 判断）。"""
        return self._expected_count > 0

    def setup_barrier(self, count: int):
        """初始化并行执行，等待 count 个 agent 注册后统一执行。"""
        if count <= 0:
            _logger.warning("setup_barrier: count=%d <= 0，跳过 barrier 设置", count)
            return
        self._pending_specs.clear()
        self._results.clear()
        self._expected_count = count
        self._registered_count = 0
        self._all_done.clear()
        self._barrier_deadline = time.monotonic() + _BARRIER_TIMEOUT

    async def register_and_wait(self) -> None:
        """
        注册当前协程，等待全部 agent 注册完成后执行。

        设计要点：
        - 最后一个注册的协程自动触发 _execute_all()
        - 其他协程通过 asyncio.Event.wait() 纯异步等待，不消耗线程池工人
        - barrier 带 _BARRIER_TIMEOUT 兜底超时：任一协程在注册前抛异常时，
          其余等待者不会永久阻塞；超时后主动触发 _execute_all 唤醒全部等待者
        """
        if self._expected_count <= 0:
            return
        async with self._agents_lock:
            self._registered_count += 1
            all_registered = (self._registered_count >= self._expected_count)
        if all_registered:
            await self._execute_all()
            return
        # 未全部注册 → 带超时等待（remaining 随 deadline 递减，防永久阻塞）
        remaining = max(0.0, self._barrier_deadline - time.monotonic())
        try:
            # asyncio.Event.wait() 不支持 timeout 参数，用 wait_for 实现带超时等待
            await asyncio.wait_for(self._all_done.wait(), timeout=remaining)
            done = True
        except asyncio.TimeoutError:
            done = False
        if done:
            return
        # 超时兜底：未注册协程永远不会到达 → 主动触发执行唤醒等待者
        if self._registered_count >= 1 and not self._executing:
            await self._execute_all()
        elif self._executing:
            # 已有协程正在执行 → 等待其完成（_all_done 由最外层 finally 保证）
            await self._all_done.wait()
        # _registered_count == 0（防御分支，正常注册后不可达）→ 直接返回

    def add_agent(self, description: str, prompt: str, agent_type: str = "execute",
                  model: str = None, tool_label: str = None) -> int:
        """注册一个 agent spec，返回其在结果列表中的索引。

        Args:
            description: Agent 描述
            prompt: 完整指令
            agent_type: 子Agent 类型（默认 execute，后续可扩展）
            model: 模型名（可选）
            tool_label: 所属 dispatch_agent 工具的 label，用于前端路由到正确容器
        """
        idx = len(self._pending_specs)
        self._pending_specs.append({
            _DESCRIPTION_KEY: description,
            "prompt": prompt,
            "agent_type": agent_type,
            "model": model,
            "tool_label": tool_label,
        })
        return idx

    def get_result(self, index: int) -> Dict[str, Any]:
        """获取指定索引的 agent 执行结果。"""
        if 0 <= index < len(self._results):
            return self._results[index]
        spec = self._pending_specs[index] if index < len(self._pending_specs) else {}
        return {
            _LABEL_KEY: f"agent-{index + 1}",
            _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, "?"),
            _RESULT_KEY: "",
            _ERROR_KEY: "内部错误：子代理结果尚未就绪",
        }

    async def _execute_all(self):
        """所有 agent 注册完毕后，统一创建 SubAgent 并并发执行。

        异常安全保证：
        - self._executing = True → False 由最外层 finally 保证
        - self._all_done.set() 由最外层 finally 保证（防 pre-try 异常泄漏）
        - 核心逻辑（_run_agents）由内层 try-finally 保护
        """
        self._executing = True
        # ★ 激活 SubAgent 面板控制器（消费 EventBus 事件并渲染面板帧）
        #   ——经 subagent/ 聚合门面统一入口（2026-08-05 出口收敛）
        from ..tui.subagent import SubAgentPanelController as _PanelCtrl
        _panel = _PanelCtrl.get_default()
        _panel.ensure_active()

        try:
            self._spawner.render_display(self._pending_specs)

            from ..tui.events import EventBusDisplayProxy as _EventBusDisplayProxy
            from ..tui.events import DisplayEventBus as _DisplayEventBus
            from ..tui.events.event_types import AgentAddedEvent as _AgentAddedEvent

            display = _EventBusDisplayProxy(max_history=self.max_history)

            # ★ 先发布 AgentAddedEvent，让前端提前创建 agent DOM 和 activeAgents 条目
            #   这样后续统一批量发布的 AgentResultEvent 才能被前端正确处理
            #   （handleAgentResult 依赖 activeAgents[label] 存在，否则丢弃结果）
            for i, spec in enumerate(self._pending_specs):
                label = f"agent-{i + 1}"
                desc = spec.get(_DESCRIPTION_KEY, label)
                dispatch_label = spec.get("tool_label", "")
                _DisplayEventBus.get_default().publish(_AgentAddedEvent(
                    label=label, description=desc, status="running", source="parallel",
                    dispatch_label=dispatch_label,
                    agent_type=spec.get(_AGENT_TYPE_KEY, "execute"),
                ))

            try:
                coro = self._run_agents(self._pending_specs, display)
                self._results = await self._execute_with_error_handling(
                    coro, self._pending_specs, display, is_batch=True,
                )
            finally:
                # 内层 finally：核心逻辑完成后立即释放 barrier
                self._all_done.set()
        finally:
            # 最外层 finally：无论 render_display / AgentAddedEvent / _run_agents
            # 哪个阶段抛出异常，都确保 barrier 释放 + _executing 标志复位 + 面板清理
            self._all_done.set()
            self._executing = False
            _panel.stop(clear_panel=True)

    # -- 独立模式 --

    async def _execute_with_error_handling(
        self, coro, specs: List[Dict[str, Any]], display: EventBusDisplayProxy,
        *, is_batch: bool,
    ) -> List[Dict[str, Any]]:
        """封装 try/except/finally 错误处理模式，消除 _execute_all / run 重复。

        Args:
            coro: 主协程（通常是 self._run_agents(specs, display)）
            specs: agent specs 列表（用于构造降级结果）
            display: EventBusDisplayProxy 实例
            is_batch: True=_execute_all 批量模式, False=run 独立模式

        Returns:
            结果列表 [{label, description, result, error}]
        """
        error_prefix = "_execute_all 异常" if is_batch else "独立模式异常"
        # ── 日志/错误键前缀（与 safe_restore 前缀不同） ──────
        # 原 run() 日志用 "独立模式"，safe_restore 用 "run"
        log_prefix = "_execute_all" if is_batch else "独立模式"
        trace_prefix = "_execute_all" if is_batch else "run"
        mode_name = "批量模式" if is_batch else "独立模式"

        results: list | None = None
        # BUG-A1：取消路径发布去重标志 — CancelledError 分支已执行
        # publish_summary 后置位，finally 跳过重复发布。
        cancel_output_done = False
        try:
            results = await coro
        except asyncio.CancelledError:
            _logger.warning("%s 被取消，降级为空结果", mode_name)
            results = [
                {_LABEL_KEY: f"agent-{i+1}",
                 _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, f"子任务 {i+1}"),
                 _RESULT_KEY: "", _ERROR_KEY: "cancelled",
                 _AGENT_TYPE_KEY: spec.get("agent_type", "execute")}
                for i, spec in enumerate(specs)
            ] if specs else []

            # ★ 在 CancelledError 传播前执行输出逻辑，避免 finally 中
            #   asyncio.to_thread 被取消导致输出丢失
            try:
                await display.await_stop(timeout=_TIMEOUT)
            except Exception:
                _logger.exception("%s 取消路径 await_stop 异常", log_prefix)

            # ★ sys.stdout 泄漏检测
            try:
                safe_restore_stdout(
                    f"{trace_prefix} 取消路径检测到 sys.stdout 泄漏 (孤立 _SharedCapture)"
                )
            except Exception:
                _logger.warning("%s 取消路径 stdout 泄漏检测异常",
                                trace_prefix, exc_info=True)

            if results:
                self._spawner.publish_summary(results)
            cancel_output_done = True
            raise
        except Exception as e:
            _logger.error("%s: %s", error_prefix, e, exc_info=True)
            results = [
                {_LABEL_KEY: f"agent-{i+1}",
                 _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, f"子任务 {i+1}"),
                 _RESULT_KEY: "", _ERROR_KEY: f"{error_prefix}: {e}",
                 _AGENT_TYPE_KEY: spec.get("agent_type", "execute")}
                for i, spec in enumerate(specs)
            ] if specs else [{_LABEL_KEY: "?", _DESCRIPTION_KEY: "?",
                              _RESULT_KEY: "", _ERROR_KEY: f"{error_prefix}: {e}",
                              _AGENT_TYPE_KEY: "execute"}]
        finally:
            # 用 None 哨兵检查 results 是否已被赋值
            if results is None:
                results = [
                    {_LABEL_KEY: f"agent-{i+1}",
                     _DESCRIPTION_KEY: spec.get(_DESCRIPTION_KEY, f"子任务 {i+1}"),
                     _RESULT_KEY: "", _ERROR_KEY: "结果未就绪",
                     _AGENT_TYPE_KEY: spec.get("agent_type", "execute")}
                    for i, spec in enumerate(specs)
                ]

            # 停止 display（终止刷新线程 + 渲染最终帧）
            try:
                await display.await_stop(timeout=5.0 if is_batch else _TIMEOUT)
            except Exception:
                _logger.exception("%s await_stop 异常", log_prefix)

            # ★ 批量模式：停止 dispatch_agent 的 Spinner
            if is_batch and not self._is_web and results:
                parent_display = getattr(self.parent, 'display', None)
                if parent_display is not None:
                    for spec in specs:
                        dispatch_label = spec.get("tool_label", "")
                        if dispatch_label:
                            try:
                                parent_display.tool_done(
                                    dispatch_label, "dispatch_agent", success=True,
                                )
                            except Exception:
                                _logger.warning("dispatch_agent tool_done 异常", exc_info=True)

            # ★ sys.stdout 泄漏检测
            try:
                safe_restore_stdout(
                    f"{trace_prefix} 检测到 sys.stdout 泄漏 (孤立 _SharedCapture)"
                )
            except Exception:
                _logger.warning("%s stdout 泄漏检测异常", trace_prefix, exc_info=True)

            # 统一批量发布 AgentResultEvent
            # BUG-A1：取消路径已发布过，finally 跳过避免重复发布
            if results and not cancel_output_done:
                self._spawner.publish_summary(results)
        return results

    async def run(self, agent_specs: List[Dict[str, Any]], max_workers: int | None = None) -> List[Dict[str, Any]]:
        """
        并行运行多个子 Agent。

        agent_specs: [{_DESCRIPTION_KEY: str, "prompt": str, "model": str (可选)}]
        max_workers: 最大并行数，默认 None（无限制，等于 task 数量）
        返回: [{_LABEL_KEY: str, _DESCRIPTION_KEY: str, _RESULT_KEY: str, _ERROR_KEY: str}]
        """
        from ..tui.subagent import SubAgentPanelController as _PanelCtrl
        _panel = _PanelCtrl.get_default()
        _panel.ensure_active()

        try:
            if not self._is_web:
                self._spawner.render_display(agent_specs)

            from ..tui.events import EventBusDisplayProxy as _EventBusDisplayProxy
            display = _EventBusDisplayProxy(max_history=self.max_history)
            coro = self._run_agents(agent_specs, display)
            return await self._execute_with_error_handling(
                coro, agent_specs, display, is_batch=False,
            )
        finally:
            _panel.stop(clear_panel=True)

    async def _run_agents(self, specs: List[Dict[str, Any]], display: EventBusDisplayProxy) -> List[Dict[str, Any]]:
        """创建 SubAgent 列表 → gather 执行 → 结果收集

        提取自 run() 和 _execute_all() 的公共逻辑。

        Args:
            specs: agent specs 列表
            display: EventBusDisplayProxy 实例

        Returns:
            结果列表 [{"label", "description", "result", "error"}]
        """
        agents: List[SubAgent] = []
        for i, spec in enumerate(specs):
            sa = self._spawner.spawn(spec, i, display)
            agents.append(sa)

        display.start()
        coros = [self._run_one(sa, display, stagger=i) for i, sa in enumerate(agents)]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        results = []
        for r in raw_results:
            if isinstance(r, BaseException):
                if isinstance(r, asyncio.CancelledError):
                    _logger.info("SubAgent %s was cancelled (expected)", getattr(r, 'label', '?'))
                else:
                    _logger.error("SubAgent task failed with: %s", r)
                results.append({_LABEL_KEY: "?", _DESCRIPTION_KEY: "?",
                               _RESULT_KEY: "", _ERROR_KEY: str(r)})
            else:
                results.append(r)
        return results

    async def _run_one(self, sa: SubAgent, display: EventBusDisplayProxy, stagger: int = 0) -> Dict[str, Any]:
        if stagger > 0:
            if self._config_port is not None:
                stagger_min = self._config_port.get_stagger_min_delay()
                stagger_max = self._config_port.get_stagger_max_delay()
            else:
                from ..config import STAGGER_MIN_DELAY, STAGGER_MAX_DELAY  # 兼容回退
                stagger_min, stagger_max = STAGGER_MIN_DELAY, STAGGER_MAX_DELAY
            # 限制最大总延迟不超过 STAGGER_MAX_DELAY，避免大量并发时线性累积
            base = random.uniform(stagger_min, stagger_max)
            delay = min(stagger * base, stagger_max * 3)
            await asyncio.sleep(delay)
        try:
            from ..tui.events import DisplayEventBus as _DisplayEventBus
            from ..tui.events.event_types import AgentStatusChanged as _AgentStatusChanged

            result = await sa.run()
            display.update_agent_status(sa.label, "done")
            _DisplayEventBus.get_default().publish(_AgentStatusChanged(
                label=sa.label, status="done", source="parallel",
            ))
            display.set_result(sa.label, result_text=result)
            # AgentResultEvent 不再逐个发布，待全部 subagent 完成后统一批量发布
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: result, _ERROR_KEY: "",
                    _AGENT_TYPE_KEY: sa.agent_type}
        except asyncio.CancelledError:
            _logger.warning("SubAgent %s 被取消", sa.label)
            display.update_model_phase(sa.label, "error", "cancelled")
            display.update_agent_status(sa.label, "fail")
            display.set_result(sa.label, error="cancelled")
            from ..tui.events import DisplayEventBus as _DisplayEventBus
            from ..tui.events.event_types import AgentStatusChanged as _AgentStatusChanged
            _DisplayEventBus.get_default().publish(_AgentStatusChanged(
                label=sa.label, status="fail", source="parallel",
            ))
            # 不 raise，改为返回结果 dict，保证 agent 身份不丢失
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: "", _ERROR_KEY: "cancelled",
                    _AGENT_TYPE_KEY: sa.agent_type}
        except Exception as e:
            from ..tui.events import DisplayEventBus as _DisplayEventBus
            from ..tui.events.event_types import AgentStatusChanged as _AgentStatusChanged

            _logger.error("SubAgent %s failed: %s", sa.label, e)
            display.update_model_phase(sa.label, "error", str(e))
            display.update_agent_status(sa.label, "fail")
            display.set_result(sa.label, error=str(e))
            _DisplayEventBus.get_default().publish(_AgentStatusChanged(
                label=sa.label, status="fail", source="parallel",
            ))
            # AgentResultEvent 不再逐个发布，待全部 subagent 完成后统一批量发布
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: "", _ERROR_KEY: str(e),
                    _AGENT_TYPE_KEY: sa.agent_type}
