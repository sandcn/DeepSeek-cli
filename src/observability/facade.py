"""可观测性门面 — 统一指标/追踪/遥测日志入口

聚合 MetricsCollector + Tracer + Telemetry 日志。
实现 ObservabilityPort 接口，可直接作为端口注入核心层。
支持 Prometheus 格式导出。
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from ..core.telemetry import (
    MetricsCollector,
    Tracer,
    get_default_collector,
    get_default_tracer,
)
from ..core.telemetry.trace_context import (
    get_current_trace_id as _get_trace_id,
    generate_trace_id as _gen_trace_id,
)
from ..core.ports.observability import ObservabilityPort

_logger = logging.getLogger(__name__)


class ObservabilityFacade(ObservabilityPort):
    """可观测性门面

    实现 ObservabilityPort 接口，聚合指标收集、调用链追踪、遥测日志三大能力。

    使用方式:
        obs = ObservabilityFacade()
        obs.counter("model.calls", 1)
        with obs.span("task"):
            ...
    """

    def __init__(
        self,
        metrics: Optional[MetricsCollector] = None,
        tracer: Optional[Tracer] = None,
    ):
        super().__init__()
        self._metrics = metrics or get_default_collector()
        self._tracer = tracer or get_default_tracer()
        self._start_time: float = 0.0

    # ── 指标（Metrics — ObservabilityPort 实现） ────────────

    def counter(self, name: str, value: int = 1) -> None:
        """递增计数器"""
        self._metrics.counter(name, value)

    def histogram(self, name: str, value: float) -> None:
        """记录直方图观测值"""
        self._metrics.histogram(name, value)

    def gauge(self, name: str, value: float) -> None:
        """设置仪表盘值"""
        self._metrics.gauge(name, value)

    # ── 追踪（Tracing — ObservabilityPort 实现） ────────

    def start_span(self, name: str):
        """开始一个追踪 span"""
        return self._tracer.start_span(name)

    def end_span(self):
        """结束当前追踪 span"""
        return self._tracer.end_span()

    def span(self, name: str):
        """上下文管理器形式的追踪 span"""
        return self._tracer.span(name)

    # ── Trace ID（ObservabilityPort 实现） ────────────────

    def get_current_trace_id(self) -> str:
        """获取当前 trace_id"""
        return _get_trace_id()

    def generate_trace_id(self) -> str:
        """生成新的 trace_id"""
        return _gen_trace_id()

    # ── 向后兼容别名 ──────────────────────────────────────

    def trace_id(self) -> str:
        """向后兼容：get_current_trace_id 的别名"""
        return self.get_current_trace_id()

    def new_trace_id(self) -> str:
        """向后兼容：generate_trace_id 的别名"""
        return self.generate_trace_id()

    # ── 子组件访问（向后兼容） ─────────────────────────

    @property
    def metrics(self) -> MetricsCollector:
        """获取底层 MetricsCollector 实例"""
        return self._metrics

    @property
    def tracer(self) -> Tracer:
        """获取底层 Tracer 实例"""
        return self._tracer

    # ── 生命周期 ────────────────────────────────────────

    def start(self) -> None:
        """记录启动时间"""
        import time
        self._start_time = time.time()

    def uptime(self) -> float:
        """获取运行时长（秒）"""
        import time
        if self._start_time == 0:
            return 0.0
        return time.time() - self._start_time

    # ── 报告 ────────────────────────────────────────────

    def metrics_report(self) -> str:
        """获取指标文本报告"""
        return self._metrics.report()

    def trace_report(self) -> str:
        """获取追踪文本报告"""
        return self._tracer.report()

    def snapshot(self) -> dict:
        """获取完整可观测性快照"""
        return {
            "uptime_seconds": self.uptime(),
            "trace_id": self.get_current_trace_id(),
            "metrics": self._metrics.snapshot(),
            "traces": self._tracer.snapshot(),
        }

    # ── 审计日志（高层编排方法） ───────────────────────

    def record_model_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """记录模型调用到遥测日志"""
        try:
            from ..api.telemetry import record_call
            record_call(model, input_tokens, output_tokens, duration_ms)
        except Exception:
            _logger.warning("遥测日志记录失败", exc_info=True)

        # 同时记录到指标
        self.counter("model.calls", 1)
        self.counter("model.input_tokens", input_tokens)
        self.counter("model.output_tokens", output_tokens)
        self.histogram("model.latency_ms", duration_ms)
        if not success:
            self.counter("model.errors", 1)

    def record_tool_call(
        self,
        tool_name: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        """记录工具调用到指标"""
        self.counter(f"tool.{tool_name}.calls", 1)
        self.histogram(f"tool.{tool_name}.latency_ms", duration_ms)
        if not success:
            self.counter(f"tool.{tool_name}.errors", 1)


# ── 全局单例门面 ────────────────────────────────────────

_default_facade: ObservabilityFacade | None = None
_facade_lock = threading.RLock()


def get_default_facade() -> ObservabilityFacade:
    """获取全局默认可观测性门面（线程安全单例）"""
    global _default_facade
    if _default_facade is None:
        with _facade_lock:
            if _default_facade is None:
                _default_facade = ObservabilityFacade()
    return _default_facade


def reset_default_facade() -> None:
    """重置全局默认门面（主要用于测试）"""
    global _default_facade
    with _facade_lock:
        _default_facade = None
