"""可观测性端口适配器 — DefaultObservabilityAdapter、MockObservabilityAdapter

包装 src.core.telemetry 中的 MetricsCollector 和 Tracer，
实现 ObservabilityPort 接口。使用延迟导入避免模块加载时触发全局单例初始化。
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Generator

from ..ports.observability import ObservabilityPort

_logger = logging.getLogger(__name__)


class DefaultObservabilityAdapter(ObservabilityPort):
    """默认可观测性适配器 — 包装 MetricsCollector + Tracer

    使用延迟导入获取全局默认实例，避免在 import 时触发模块加载和全局单例构造。
    也可通过构造函数注入自定义的 MetricsCollector 和 Tracer 实例（测试用）。
    """

    def __init__(self, metrics=None, tracer=None):
        """初始化适配器

        Args:
            metrics: MetricsCollector 实例（可选，默认使用 get_default_collector()）
            tracer: Tracer 实例（可选，默认使用 get_default_tracer()）
        """
        self._metrics = metrics
        self._tracer = tracer
        self._resolved = False
        self._lock = threading.RLock()

    def _resolve(self) -> None:
        """延迟解析全局默认实例（线程安全，仅执行一次）"""
        if self._resolved:
            return
        with self._lock:
            if self._resolved:
                return
            if self._metrics is None:
                from ..telemetry.metrics import get_default_collector
                self._metrics = get_default_collector()
            if self._tracer is None:
                from ..telemetry.tracer import get_default_tracer
                self._tracer = get_default_tracer()
            self._resolved = True

    # ── 指标（Metrics） ────────────────────────────────

    def counter(self, name: str, value: int = 1) -> None:
        self._resolve()
        try:
            self._metrics.counter(name, value)
        except Exception:
            _logger.warning("Metric counter 失败: %s", name, exc_info=True)

    def histogram(self, name: str, value: float) -> None:
        self._resolve()
        try:
            self._metrics.histogram(name, value)
        except Exception:
            _logger.warning("Metric histogram 失败: %s", name, exc_info=True)

    def gauge(self, name: str, value: float) -> None:
        self._resolve()
        try:
            self._metrics.gauge(name, value)
        except Exception:
            _logger.warning("Metric gauge 失败: %s", name, exc_info=True)

    # ── 追踪（Tracing） ────────────────────────────────

    def start_span(self, name: str) -> Any:
        self._resolve()
        return self._tracer.start_span(name)

    def end_span(self) -> Any:
        self._resolve()
        return self._tracer.end_span()

    @contextmanager
    def span(self, name: str) -> Generator[Any, None, None]:
        self._resolve()
        with self._tracer.span(name) as span:
            yield span

    # ── Trace ID ───────────────────────────────────────

    def get_current_trace_id(self) -> str:
        from ..telemetry.trace_context import get_current_trace_id as _get
        return _get()

    def generate_trace_id(self) -> str:
        from ..telemetry.trace_context import generate_trace_id as _gen
        return _gen()


class MockObservabilityAdapter(ObservabilityPort):
    """Mock 可观测性适配器 — 用于测试

    记录所有调用到 self.calls 列表，不触发真实指标收集。
    """

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.spans: list[str] = []
        self._trace_id: str = "mock_trace_001"

    def _record(self, method: str, *args, **kwargs) -> None:
        self.calls.append((method, args, kwargs))

    # ── 指标 ───────────────────────────────────────────

    def counter(self, name: str, value: int = 1) -> None:
        self._record("counter", name, value)

    def histogram(self, name: str, value: float) -> None:
        self._record("histogram", name, value)

    def gauge(self, name: str, value: float) -> None:
        self._record("gauge", name, value)

    # ── 追踪 ───────────────────────────────────────────

    def start_span(self, name: str) -> Any:
        self._record("start_span", name)
        span_id = f"mock_span_{len(self.spans)}"
        self.spans.append(span_id)
        return _MockSpan(span_id, name)

    def end_span(self) -> Any:
        self._record("end_span")

    @contextmanager
    def span(self, name: str) -> Generator[Any, None, None]:
        self._record("span", name)
        span_id = f"mock_span_{len(self.spans)}"
        self.spans.append(span_id)
        yield _MockSpan(span_id, name)

    # ── Trace ID ───────────────────────────────────────

    def get_current_trace_id(self) -> str:
        return self._trace_id

    def generate_trace_id(self) -> str:
        return f"mock_trace_{len(self.calls)}"


class _MockSpan:
    """Mock Span — 用于 MockObservabilityAdapter"""

    def __init__(self, span_id: str, name: str):
        self.span_id = span_id
        self.name = name
        self.attrs: dict = {}
        self.status = "ok"
        self.error_message = ""

    def set_attribute(self, key: str, value: Any) -> None:
        self.attrs[key] = value

    def set_status(self, status: str, message: str = "") -> None:
        self.status = status
        if message:
            self.error_message = message
