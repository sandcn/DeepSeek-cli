"""
ParallelExecutor — 使用 asyncio.gather 并行调度多个 SubAgent

subagent 工具直接后台执行（无 background 参数、无前台阻塞模式）：后台
subagent 经本执行器（run 独立模式）在 asyncio 后台任务中运行单个 SubAgent。

★ 共享批量 barrier 模式已随 subagent 工具 background 参数一并移除
（2026-08-19）：subagent 每次调用独立后台执行，不再共享 ParallelExecutor。
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import List, Dict, Any

from .internal.agent._capture_manager import _safe_restore as safe_restore_stdout
from .internal.agent._subagent_spawner import SubAgentSpawner
from .subagent import SubAgent

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..tui.events import EventBusDisplayProxy

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

# ── 结果字典键常量 ─────────────────────────────────
_DESCRIPTION_KEY = "description"
_ERROR_KEY = "error"
_LABEL_KEY = "label"
_RESULT_KEY = "result"
_AGENT_TYPE_KEY = "agent_type"


# ── 终端尺寸查询 ─────────────────────────────────────
# 共享 get_terminal_width（src/core/_terminal.py）：优先 /dev/tty ioctl 查询，
# 不能依赖 shutil.get_terminal_size()（Android Termux 上返回陈旧环境变量值）。


class ParallelExecutor:
    """并行执行多个 SubAgent，统一管理显示（run 独立模式）。

    subagent 工具直接后台执行时，每次调用经本执行器 ``run()`` 在独立
    asyncio 后台任务中运行单个（或多个）SubAgent。共享批量 barrier 模式
    已随 subagent 工具 background 参数一并移除。
    """

    def __init__(self, parent_agent, max_history: int = 3, agent_factory=None,
                 config_port=None):
        self.parent = parent_agent
        self.max_history = max_history
        self._agent_factory = agent_factory or SubAgent
        self._config_port = config_port

        # SubAgent 创建 + 显示委托
        self._spawner = SubAgentSpawner(parent_agent, self._agent_factory)

    # -- 独立模式 --

    async def _execute_with_error_handling(
        self, coro, specs: List[Dict[str, Any]], display: EventBusDisplayProxy,
    ) -> List[Dict[str, Any]]:
        """封装 try/except/finally 错误处理模式，消除重复（run 独立模式）。

        Args:
            coro: 主协程（通常是 self._run_agents(specs, display)）
            specs: agent specs 列表（用于构造降级结果）
            display: EventBusDisplayProxy 实例

        Returns:
            结果列表 [{label, description, result, error}]
        """
        error_prefix = "独立模式异常"
        # ── 日志/错误键前缀（与 safe_restore 前缀不同） ──────
        log_prefix = "独立模式"
        trace_prefix = "run"
        mode_name = "独立模式"

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
            # 注（review P3）：取消路径与 finally 各调用一次 await_stop——
            # EventBusDisplayProxy.await_stop 为幂等实现（首次停止刷新线程，
            # 再次调用安全返回），不会重复渲染
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
                await display.await_stop(timeout=_TIMEOUT)
            except Exception:
                _logger.exception("%s await_stop 异常", log_prefix)

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
            self._spawner.render_display(agent_specs)

            from ..tui.events import EventBusDisplayProxy as _EventBusDisplayProxy
            display = _EventBusDisplayProxy(max_history=self.max_history)
            coro = self._run_agents(agent_specs, display)
            return await self._execute_with_error_handling(
                coro, agent_specs, display,
            )
        finally:
            _panel.stop(clear_panel=True)

    async def _run_agents(self, specs: List[Dict[str, Any]], display: EventBusDisplayProxy) -> List[Dict[str, Any]]:
        """创建 SubAgent 列表 → gather 执行 → 结果收集

        供 run() 独立模式调用。

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
            # ★ 2026-08-16（轨迹 Trace 嵌套）：把 SubAgent 实例注册到面板
            #   控制器——槽位存 messages/prompt 引用（实时增长），轨迹视图
            #   Enter subagent 记录时显示 subagent 轨迹（与 mainagent 同构：
            #   system/user/assistant/tool 消息 → 台账 + 检查器）。注册失败
            #   非致命（面板无槽位/异常时跳过，轨迹回退槽位活动记录）。
            try:
                from ..tui.subagent import SubAgentPanelController as _PanelCtrl
                _PanelCtrl.get_default().register_subagent(sa.label, sa)
            except Exception:
                _logger.debug("注册 SubAgent 到面板控制器失败: %s", sa.label, exc_info=True)

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
            from ..tui.events.event_types import AgentStatusChanged as _AgentStatusChanged
            from ..tui.events.publish import emit

            result = await sa.run()
            display.update_agent_status(sa.label, "done")
            emit(_AgentStatusChanged(
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
            from ..tui.events.event_types import AgentStatusChanged as _AgentStatusChanged
            emit(_AgentStatusChanged(
                label=sa.label, status="fail", source="parallel",
            ))
            # 不 raise，改为返回结果 dict，保证 agent 身份不丢失
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: "", _ERROR_KEY: "cancelled",
                    _AGENT_TYPE_KEY: sa.agent_type}
        except Exception as e:
            from ..tui.events.event_types import AgentStatusChanged as _AgentStatusChanged

            _logger.error("SubAgent %s failed: %s", sa.label, e)
            display.update_model_phase(sa.label, "error", str(e))
            display.update_agent_status(sa.label, "fail")
            display.set_result(sa.label, error=str(e))
            emit(_AgentStatusChanged(
                label=sa.label, status="fail", source="parallel",
            ))
            # AgentResultEvent 不再逐个发布，待全部 subagent 完成后统一批量发布
            return {_LABEL_KEY: sa.label, _DESCRIPTION_KEY: sa.description,
                    _RESULT_KEY: "", _ERROR_KEY: str(e),
                    _AGENT_TYPE_KEY: sa.agent_type}
