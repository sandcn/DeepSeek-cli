"""异步可观测性中间件 — 采集指标 + 调用链追踪"""

import logging

from ..telemetry import get_default_collector, get_default_tracer
from ..pipeline import AsyncMiddleware, PipelineContext

_logger = logging.getLogger(__name__)


class _AsyncObservabilityMiddleware(AsyncMiddleware):
    """异步可观测性中间件 — 采集指标 + 调用链追踪

    与 _ObservabilityMiddleware 功能对等，但使用 async hooks，
    适配 Pipeline.run_round_async() 的异步执行路径。
    """

    def __init__(self):
        super().__init__()
        self._metrics = get_default_collector()
        self._tracer = get_default_tracer()

    @property
    def name(self) -> str:
        return "AsyncObservability"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        try:
            # 开始模型调用的追踪 Span
            self._tracer.start_span("model.call_async")
            # 增加模型调用计数器
            self._metrics.counter("model.calls", 1)
        except Exception:
            _logger.exception("AsyncObservability.before_model_call 异常")

    async def after_model_call(self, ctx: PipelineContext) -> None:
        try:
            # 结束追踪 Span
            span = self._tracer.end_span()
            if span:
                span.set_attribute("model", ctx.agent.model)
                span.set_attribute("tool_calls", len(ctx.tool_calls))
                span.set_attribute("input_tokens", ctx.usage.get("input", 0))
                span.set_attribute("output_tokens", ctx.usage.get("output", 0))

            # 记录指标
            usage = ctx.usage
            if usage:
                self._metrics.counter("tokens.input", usage.get("input", 0))
                self._metrics.counter("tokens.output", usage.get("output", 0))
                if usage.get("input", 0) > 0 or usage.get("output", 0) > 0:
                    self._metrics.histogram("model.latency_ms",
                                            usage.get("latency_ms", 0))

            total_chars = sum(len(m.get("content", "") or "") for m in ctx.agent.messages)
            self._metrics.gauge("context.chars", total_chars)
        except Exception:
            _logger.exception("AsyncObservability.after_model_call 异常")

    async def on_exception(self, ctx: PipelineContext, exc: Exception) -> None:
        try:
            # 清理 before_model_call 中 start_span 推入的 span，防止泄漏
            self._tracer.end_span()
        except Exception:
            _logger.exception("AsyncObservability.on_exception 异常")

    async def on_round_complete(self, ctx: PipelineContext) -> None:
        try:
            self._metrics.counter("rounds", 1)
            self._metrics.gauge("context.messages", len(ctx.agent.messages))

            if ctx.interrupted:
                self._metrics.counter("interrupts", 1)
        except Exception:
            _logger.exception("AsyncObservability.on_round_complete 异常")
