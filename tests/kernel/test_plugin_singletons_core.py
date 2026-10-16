"""事件/输出/缓存/可观测单例收敛为内核服务测试。

覆盖 A 组「内核服务单例彻底化」：
- ``get_default_bus`` / ``DisplayEventBus.get_default`` /
  ``DisplayEventBusAdapter.get_default`` 内核优先返回 ``ctx.events`` 独占实例；
- ``DefaultOutputAdapter.get_default`` / ``get_default_output_port`` 返回
  ``ctx.output`` 独占实例；
- ``get_default_cache`` 返回 ``ctx.cache`` 独占实例；
- ``get_default_facade`` / ``get_default_collector`` / ``get_default_tracer``
  返回 ``ctx.observability`` 独占实例。
"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def headless_kernel():
    kernel = await build_kernel("headless")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_core_event_bus_is_kernel_service(headless_kernel):
    from src.core.events.event_bus import get_default_bus

    events = headless_kernel.resolve_service("events")
    assert get_default_bus() is events.bus


async def test_display_event_bus_is_kernel_service(headless_kernel):
    from src.core.events.display_bus import DisplayEventBus

    events = headless_kernel.resolve_service("events")
    assert DisplayEventBus.get_default() is events.display_bus


async def test_event_adapter_is_kernel_service(headless_kernel):
    from src.core.adapters.events import DisplayEventBusAdapter

    events = headless_kernel.resolve_service("events")
    assert DisplayEventBusAdapter.get_default() is events.event_adapter()
    assert DisplayEventBusAdapter.get_default() is events.event_adapter()


async def test_output_port_is_kernel_service(headless_kernel):
    from src.core.adapters.output import DefaultOutputAdapter, get_default_output_port

    port = headless_kernel.resolve_service("output").port
    assert DefaultOutputAdapter.get_default() is port
    assert get_default_output_port() is port


async def test_cache_is_kernel_service(headless_kernel):
    from src.core.cache import get_default_cache

    assert get_default_cache() is headless_kernel.resolve_service("cache").cache


async def test_observability_facade_is_kernel_service(headless_kernel):
    from src.observability.facade import get_default_facade

    service = headless_kernel.resolve_service("observability")
    assert get_default_facade() is service.provider
    assert service.facade is service.provider


async def test_collector_and_tracer_are_kernel_service(headless_kernel):
    from src.core.telemetry.metrics import get_default_collector
    from src.core.telemetry.tracer import get_default_tracer

    service = headless_kernel.resolve_service("observability")
    assert get_default_collector() is service.collector
    assert get_default_tracer() is service.tracer


async def test_event_bus_publish_through_kernel(headless_kernel):
    from src.core.events.event_bus import get_default_bus

    seen = []
    get_default_bus().subscribe("demo.event", lambda event: seen.append(event.data))
    get_default_bus().publish("demo.event", {"x": 1})
    assert seen == [{"x": 1}]


def test_singletons_fall_back_without_kernel():
    from src.kernel import set_current_kernel
    from src.core.events.event_bus import get_default_bus
    from src.core.events.display_bus import DisplayEventBus
    from src.core.cache import get_default_cache
    from src.core.adapters.output import DefaultOutputAdapter
    from src.observability.facade import get_default_facade

    set_current_kernel(None)
    assert get_default_bus() is get_default_bus()
    assert DisplayEventBus.get_default() is DisplayEventBus.get_default()
    assert get_default_cache() is get_default_cache()
    assert DefaultOutputAdapter.get_default() is DefaultOutputAdapter.get_default()
    assert get_default_facade() is get_default_facade()
