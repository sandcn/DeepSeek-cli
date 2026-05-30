"""可观测性模块 — 统一 Metrics / Tracing / Logging 门面

聚合 core/telemetry（结构化指标+追踪）和 api/telemetry（遥测日志），
提供统一入口。

分层：
- ObservabilityPort — 抽象接口
- DefaultObservability — 默认实现，委托给现有模块
- ObservabilityFacade — 便捷门面类

使用方式:
    from src.observability import obs

    obs.counter("model.calls", 1)
    obs.histogram("model.latency_ms", 150.0)

    with obs.tracer.span("tool.execute") as span:
        span.set_attribute("tool", "read_file")
        ...
"""

from .facade import ObservabilityFacade, get_default_facade, reset_default_facade

obs: ObservabilityFacade = get_default_facade()

__all__ = [
    "ObservabilityFacade",
    "get_default_facade",
    "reset_default_facade",
    "obs",
]
