"""异步可观测性中间件 — 采集指标 + 调用链追踪

支持两种模式：
1. 端口模式：从 ctx.agent.get_observability_port() 获取 ObservabilityPort
2. 传统模式：回退到 get_default_collector() / get_default_tracer()
"""

import logging

from ..telemetry import get_default_collector, get_default_tracer
from ..pipeline import AsyncMiddleware, PipelineContext

_logger = logging.getLogger(__name__)


class _AsyncObservabilityMiddleware(AsyncMiddleware):
    """异步可观测性中间件 — 采集指标 + 调用链追踪

    与 _ObservabilityMiddleware 功能对等，但使用 async hooks，
    适配 Pipeline.run_round_async() 的异步执行路径。

    优先使用 ctx.agent.get_observability_port() 获取端口，
    回退到全局默认 MetricsCollector / Tracer。
    """

    def __init__(self):
        super().__init__()
        self._metrics = None  # 惰性初始化
        self._tracer = None   # 惰性初始化

    def _resolve(self, ctx: PipelineContext) -> tuple:
        """解析可观测性实现，优先从端口获取"""
        # 传统模式：使用全局单例（始终可用，作为默认路径）
        if self._metrics is None:
            self._metrics = get_default_collector()
        if self._tracer is None:
            self._tracer = get_default_tracer()

        # 端口模式：仅当 agent 明确有 get_observability_port 方法时使用
        # （检查 type 而非实例，避免 MagicMock 自动创建属性）
        agent = ctx.agent
        get_port = getattr(type(agent), 'get_observability_port', None)
        if get_port is not None and callable(get_port):
            try:
                port = agent.get_observability_port()
                if port is not None:
                    return None, None, port
            except Exception:
                _logger.debug("get_observability_port 异常，回退到传统模式")

        return self._metrics, self._tracer, None

    @property
    def name(self) -> str:
        return "AsyncObservability"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        _metrics, _tracer, _port = self._resolve(ctx)
        try:
            if _port:
                _port.counter("model.calls", 1)
            else:
                # 开始模型调用的追踪 Span
                _tracer.start_span("model.call_async")
                # 增加模型调用计数器
                _metrics.counter("model.calls", 1)
        except Exception:
            _logger.exception("AsyncObservability.before_model_call 异常")

    async def after_model_call(self, ctx: PipelineContext) -> None:
        _metrics, _tracer, _port = self._resolve(ctx)
        try:
            if _port:
                usage = ctx.usage
                if usage:
                    _port.counter("tokens.input", usage.get("input", 0))
                    _port.counter("tokens.output", usage.get("output", 0))
                    if usage.get("input", 0) > 0 or usage.get("output", 0) > 0:
                        _port.histogram("model.latency_ms",
                                        usage.get("latency_ms", 0))
                total_chars = sum(
                    len(m.get("content", "") or "") for m in ctx.agent.messages
                )
                _port.gauge("context.chars", total_chars)
            else:
                # 结束追踪 Span
                span = _tracer.end_span()
                if span:
                    span.set_attribute("model", ctx.agent.model)
                    span.set_attribute("tool_calls", len(ctx.tool_calls))
                    span.set_attribute("input_tokens", ctx.usage.get("input", 0))
                    span.set_attribute("output_tokens", ctx.usage.get("output", 0))

                # 记录指标
                usage = ctx.usage
                if usage:
                    _metrics.counter("tokens.input", usage.get("input", 0))
                    _metrics.counter("tokens.output", usage.get("output", 0))
                    if usage.get("input", 0) > 0 or usage.get("output", 0) > 0:
                        _metrics.histogram("model.latency_ms",
                                           usage.get("latency_ms", 0))

                total_chars = sum(
                    len(m.get("content", "") or "") for m in ctx.agent.messages
                )
                _metrics.gauge("context.chars", total_chars)
        except Exception:
            _logger.exception("AsyncObservability.after_model_call 异常")

    async def on_exception(self, ctx: PipelineContext, exc: Exception) -> None:
        _metrics, _tracer, _port = self._resolve(ctx)
        try:
            if not _port:
                # 清理 before_model_call 中 start_span 推入的 span，防止泄漏
                _tracer.end_span()
        except Exception:
            _logger.exception("AsyncObservability.on_exception 异常")

    async def on_round_complete(self, ctx: PipelineContext) -> None:
        _metrics, _tracer, _port = self._resolve(ctx)
        try:
            if _port:
                _port.counter("rounds", 1)
                if ctx.interrupted:
                    _port.counter("interrupts", 1)
            else:
                _metrics.counter("rounds", 1)
                _metrics.gauge("context.messages", len(ctx.agent.messages))
                if ctx.interrupted:
                    _metrics.counter("interrupts", 1)
        except Exception:
            _logger.exception("AsyncObservability.on_round_complete 异常")
