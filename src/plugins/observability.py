"""可观测性插件 — 提供 ``ctx.observability``。

「一切皆插件」：可观测性（指标 / 追踪 / trace_id 传播）作为可替换的
provider 坐在内核之上。默认 provider 是 :class:`ObservabilityFacade`
（聚合 ``MetricsCollector`` + ``Tracer`` + 遥测日志）；外部插件可经
``ctx.observability.set_provider(...)`` 替换为任意实现了
``ObservabilityPort`` 的实现（如远端上报、Null Object 关闭观测）。

Agent / Session 经本服务注入 ``ObservabilityPort``（内核缺失时回退
``DefaultObservabilityAdapter``），因此替换 provider 即改变整条观测链路。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class ObservabilityService(Service):
    """可观测性服务 — 占据 ``ctx.observability``。"""

    provide = "observability"
    name = "observability"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.telemetry.metrics import MetricsCollector
        from ..core.telemetry.tracer import Tracer
        from ..observability.facade import ObservabilityFacade

        self._collector = MetricsCollector()
        self._tracer = Tracer()
        self._facade = ObservabilityFacade(metrics=self._collector, tracer=self._tracer)
        self._provider = self._facade
        self._previous: Optional[Any] = None
        ctx.effect(lambda: self._on_unload)

    # ── Provider ─────────────────────────────────────────

    def _on_unload(self) -> None:
        self._provider = None
        self._previous = None

    @property
    def provider(self):
        return self._provider

    @property
    def collector(self):
        """本服务独占的指标收集器（``get_default_collector`` 内核真源）。"""
        return self._collector

    @property
    def tracer(self):
        """本服务独占的调用链追踪器（``get_default_tracer`` 内核真源）。"""
        return self._tracer

    @property
    def facade(self):
        """本服务独占的默认门面（provider 被替换时仍指向默认门面）。"""
        return self._facade

    def port(self):
        """供 Agent/Session 注入的 ObservabilityPort（未加载时返回 None）。"""
        return self._provider

    def set_provider(self, provider) -> Any:
        """替换 provider，返回旧 provider（None 表示服务已卸载）。"""
        previous = self._provider
        self._provider = provider
        return previous

    def restore_provider(self, provider) -> None:
        self._provider = provider

    # ── 门面转发（常用能力，便于插件自省/采集） ────────────

    def counter(self, name: str, value: int = 1) -> None:
        if self._provider is not None:
            self._provider.counter(name, value)

    def histogram(self, name: str, value: float) -> None:
        if self._provider is not None:
            self._provider.histogram(name, value)

    def gauge(self, name: str, value: float) -> None:
        if self._provider is not None:
            self._provider.gauge(name, value)

    def snapshot(self) -> dict:
        provider = self._provider
        snapshot = getattr(provider, "snapshot", None)
        return snapshot() if callable(snapshot) else {}

    def metrics_report(self) -> str:
        provider = self._provider
        report = getattr(provider, "metrics_report", None)
        return report() if callable(report) else ""

    def trace_report(self) -> str:
        provider = self._provider
        report = getattr(provider, "trace_report", None)
        return report() if callable(report) else ""


@plugin("observability", provide=["observability"])
def apply(ctx):
    return ObservabilityService(ctx)


__all__ = ["ObservabilityService", "apply"]
