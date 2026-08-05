"""可观测性端口 — 核心层与可观测性基础设施的抽象接口

定义指标收集、调用链追踪、trace_id 传播的抽象协议。
适配器实现移至 src.core.adapters.observability。

设计原则：
- 基础设施无关：核心层通过此接口使用可观测性，不直接依赖 MetricsCollector/Tracer
- 可 Mock：测试时传入 MockObservabilityAdapter 即可验证可观测性调用
- 仅覆盖核心可观测能力（指标+追踪），不包含应用层编排逻辑（如 record_model_call）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any

class ObservabilityPort(ABC):
    """可观测性抽象端口

    聚合指标收集、调用链追踪、trace_id 传播三大能力。

    使用方式:
        obs = get_observability_port()
        obs.counter("model.calls", 1)
        with obs.span("model_call") as span:
            span.set_attribute("model", "deepseek")
    """

    # ── 指标（Metrics） ────────────────────────────────

    @abstractmethod
    def counter(self, name: str, value: int = 1) -> None:
        """递增计数器

        Args:
            name: 指标名称（如 "model.calls"）
            value: 增量值，默认 1
        """
        ...

    @abstractmethod
    def histogram(self, name: str, value: float) -> None:
        """记录直方图观测值

        Args:
            name: 指标名称（如 "model.latency_ms"）
            value: 观测值
        """
        ...

    @abstractmethod
    def gauge(self, name: str, value: float) -> None:
        """设置仪表盘值（可升降）

        Args:
            name: 指标名称（如 "context.chars"）
            value: 当前值
        """
        ...

    # ── 追踪（Tracing） ────────────────────────────────

    @abstractmethod
    def start_span(self, name: str) -> Any:
        """开始一个新的 Span

        Args:
            name: Span 名称（如 "model_call"）

        Returns:
            Span 对象，具有 set_attribute(key, value) 和 set_status(status, message) 方法
        """
        ...

    @abstractmethod
    def end_span(self) -> Any:
        """结束当前活跃的 Span

        Returns:
            已结束的 Span 对象，若栈为空返回 None
        """
        ...

    @abstractmethod
    def span(self, name: str) -> AbstractContextManager:
        """上下文管理器：自动 start/end Span

        用法:
            with obs.span("model_call") as span:
                span.set_attribute("model", "deepseek")

        Args:
            name: Span 名称

        Returns:
            上下文管理器，进入时返回 Span 对象
        """
        ...

    # ── Trace ID ───────────────────────────────────────

    @abstractmethod
    def get_current_trace_id(self) -> str:
        """获取当前 asyncio 上下文的 trace_id"""
        ...

    @abstractmethod
    def generate_trace_id(self) -> str:
        """生成新的 trace_id"""
        ...