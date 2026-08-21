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
        # 直接取实例属性：真实 Agent 的 get_observability_port 是实例方法，
        # 若改用 type(agent) 取值会拿到未绑定函数（缺 self → TypeError → 被
        # except 吞掉 → 必然返回 None，观测采集整体失效）。实例属性同时覆盖
        # 真实 Agent（实例方法）与 mock agent（实例属性）两种场景。
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
                    # 仅当字段存在才记录——否则向直方图注入 0ms 采样会严重
                    # 拉低 P50/P99（多数适配器不返回 latency_ms）。
                    latency_ms = usage.get("latency_ms")
                    if latency_ms is not None:
                        port.histogram("model.latency_ms", latency_ms)
            def _msg_text(chars_total):
                content = chars_total.get("content", "") or ""
                if isinstance(content, list):
                    try:
                        from ...api.multimodal import content_to_text
                        return content_to_text(content)
                    except Exception:
                        return ""
                return content if isinstance(content, str) else ""
            total_chars = sum(len(_msg_text(m)) for m in ctx.agent.messages)
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
