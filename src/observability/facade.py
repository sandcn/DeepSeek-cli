"""可观测性门面 — 统一指标/追踪/遥测日志入口

聚合 MetricsCollector + Tracer + Telemetry 日志。
支持 Prometheus 格式导出。

v2.3 新增：支持通过 ObservabilityPort 注入可观测性实现，
使得 Agent/ChatSession 等核心层可通过端口使用可观测性，
而不直接依赖全局单例。
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
    get_current_trace_id,
    generate_trace_id,
)
from ..core.ports.observability import ObservabilityPort

_logger = logging.getLogger(__name__)


class ObservabilityFacade:
    """可观测性门面

    聚合指标收集、调用链追踪、遥测日志三大能力。

    支持两种初始化模式：
    1. 传统模式（不传 observability_port）：通过 metrics/tracer 参数注入
    2. 端口模式（传 observability_port）：通过 ObservabilityPort 注入

    使用方式:
        obs = ObservabilityFacade()
        obs.counter("model.calls", 1)
        with obs.tracer.span("task"):
            ...
    """

    def __init__(
        self,
        metrics: Optional[MetricsCollector] = None,
        tracer: Optional[Tracer] = None,
        observability_port: Optional[ObservabilityPort] = None,
    ):
        self._observability_port = observability_port
        self._metrics = metrics
        self._tracer = tracer
        self._start_time: float = 0.0

        # 端口模式：使用端口实例，忽略 metrics/tracer 参数
        if observability_port is not None:
            self._port_mode = True
        else:
            self._port_mode = False
            self._metrics = metrics or get_default_collector()
            self._tracer = tracer or get_default_tracer()

    # ── 内部代理 ──────────────────────────────────────────

    @property
    def _active_metrics(self) -> MetricsCollector:
        if self._port_mode:
            # 端口模式下通过计数器和仪表盘需要额外包装
            # 返回 None 表示走端口路径
            return None  # type: ignore
        return self._metrics

    # ── 指标（Metrics） ────────────────────────────────

    @property
    def metrics(self) -> MetricsCollector:
        return self._metrics

    def counter(self, name: str, value: int = 1) -> None:
        """递增计数器"""
        if self._port_mode:
            self._observability_port.counter(name, value)
        else:
            self._metrics.counter(name, value)

    def histogram(self, name: str, value: float) -> None:
        """记录直方图观测值"""
        if self._port_mode:
            self._observability_port.histogram(name, value)
        else:
            self._metrics.histogram(name, value)

    def gauge(self, name: str, value: float) -> None:
        """设置仪表盘值"""
        if self._port_mode:
            self._observability_port.gauge(name, value)
        else:
            self._metrics.gauge(name, value)

    # ── 追踪（Tracing） ────────────────────────────────

    @property
    def tracer(self) -> Tracer:
        return self._tracer

    def trace_id(self) -> str:
        """获取当前 trace_id"""
        if self._port_mode:
            return self._observability_port.get_current_trace_id()
        return get_current_trace_id()

    def new_trace_id(self) -> str:
        """生成新的 trace_id"""
        if self._port_mode:
            return self._observability_port.generate_trace_id()
        return generate_trace_id()

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
        if self._port_mode:
            return "(port mode) 指标报告通过 ObservablePort 获取"
        return self._metrics.report()

    def trace_report(self) -> str:
        """获取追踪文本报告"""
        if self._port_mode:
            return "(port mode) 追踪报告通过 ObservablePort 获取"
        return self._tracer.report()

    def snapshot(self) -> dict:
        """获取完整可观测性快照"""
        if self._port_mode:
            return {
                "uptime_seconds": self.uptime(),
                "trace_id": self.trace_id(),
                "mode": "port",
            }
        return {
            "uptime_seconds": self.uptime(),
            "trace_id": self.trace_id(),
            "metrics": self._metrics.snapshot(),
            "traces": self._tracer.snapshot(),
        }

    # ── 审计日志（委托给 api/telemetry） ───────────────

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
