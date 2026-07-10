"""异步可观测性中间件 — 通过 ObservabilityPort 采集指标 + 追踪

始终从 ctx.agent.get_observability_port() 获取 ObservabilityPort。
回退路径已移除 — 核心层统一通过端口访问可观测性。
"""

import logging

from ..pipeline import AsyncMiddleware, PipelineContext

_logger = logging.getLogger(__name__)


class _AsyncObservabilityMiddleware(AsyncMiddleware):
    """异步可观测性中间件 — 通过 ObservabilityPort 采集指标 + 追踪

    始终从 ctx.agent.get_observability_port() 获取 ObservabilityPort。
    若 port 不可用则静默跳过（非关键路径，不阻断主流程）。
    """

    def __init__(self):
        super().__init__()

    def _resolve(self, ctx: PipelineContext):
        """从 agent 获取 ObservabilityPort"""
        agent = ctx.agent
        # 优先检查类属性（真实 agent），回退到实例属性（mock agent）
        get_port = getattr(type(agent), 'get_observability_port', None)
        if get_port is None or not callable(get_port):
            get_port = getattr(agent, 'get_observability_port', None)
        if get_port is not None and callable(get_port):
            try:
                return get_port()
            except Exception:
                _logger.debug("get_observability_port 异常，跳过可观测性采集")
        return None

    @property
    def name(self) -> str:
        return "AsyncObservability"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        port = self._resolve(ctx)
        if port is None:
            return
        try:
            port.counter("model.calls", 1)
        except Exception:
            _logger.exception("AsyncObservability.before_model_call 异常")

    async def after_model_call(self, ctx: PipelineContext) -> None:
        port = self._resolve(ctx)
        if port is None:
            return
        try:
            usage = ctx.usage
            if usage:
                port.counter("tokens.input", usage.get("input", 0))
                port.counter("tokens.output", usage.get("output", 0))
                if usage.get("input", 0) > 0 or usage.get("output", 0) > 0:
                    port.histogram("model.latency_ms",
                                   usage.get("latency_ms", 0))
            total_chars = sum(
                len(m.get("content", "") or "") for m in ctx.agent.messages
            )
            port.gauge("context.chars", total_chars)
        except Exception:
            _logger.exception("AsyncObservability.after_model_call 异常")

    async def on_exception(self, ctx: PipelineContext, exc: Exception) -> None:
        port = self._resolve(ctx)
        if port is None:
            return
        # 端口模式无需清理 span（port 自行管理追踪生命周期）

    async def on_round_complete(self, ctx: PipelineContext) -> None:
        port = self._resolve(ctx)
        if port is None:
            return
        try:
            port.counter("rounds", 1)
            if ctx.interrupted:
                port.counter("interrupts", 1)
        except Exception:
            _logger.exception("AsyncObservability.on_round_complete 异常")
