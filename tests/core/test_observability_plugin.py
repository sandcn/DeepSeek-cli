"""可观测性插件测试 — ctx.observability 服务与 provider 替换。

覆盖：
- 服务提供默认 ObservabilityFacade；
- set_provider 替换与转发；
- Agent / Session 经内核服务注入 ObservabilityPort；
- 卸载撤销；
- 无内核回退 DefaultObservabilityAdapter。
"""

from __future__ import annotations

import pytest

from src.kernel import set_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


class _FakePort:
    def __init__(self):
        self.counters = []
        self.gauges = []
        self.histograms = []

    def counter(self, name, value=1):
        self.counters.append((name, value))

    def histogram(self, name, value):
        self.histograms.append((name, value))

    def gauge(self, name, value):
        self.gauges.append((name, value))


async def test_observability_service_present(cli_kernel):
    from src.observability import ObservabilityFacade

    service = cli_kernel.resolve_service("observability")
    assert isinstance(service.port(), ObservabilityFacade)
    assert service.provider is service.port()


async def test_set_provider_and_forwarding(cli_kernel):
    service = cli_kernel.resolve_service("observability")
    fake = _FakePort()
    previous = service.set_provider(fake)
    try:
        service.counter("a")
        service.gauge("b", 3)
        service.histogram("c", 1.5)
        assert fake.counters == [("a", 1)]
        assert fake.gauges == [("b", 3)]
        assert fake.histograms == [("c", 1.5)]
        assert service.port() is fake
    finally:
        service.set_provider(previous)


async def test_agent_injected_kernel_observability(cli_kernel):
    from src.core.adapters.kernel_runtime import active_observability_port

    port = active_observability_port()
    assert port is cli_kernel.resolve_service("observability").port()
    agent = cli_kernel.resolve_service("agent_loop").create_headless_agent()
    assert agent.get_observability_port() is port


async def test_session_uses_kernel_observability(cli_kernel):
    from src.core.session import ChatSession

    session = ChatSession()
    assert session._observability_port is cli_kernel.resolve_service("observability").port()


async def test_unload_clears_provider(cli_kernel):
    service = cli_kernel.resolve_service("observability")
    fiber = cli_kernel.fiber("observability")
    await fiber.dispose()
    await cli_kernel.settle()
    assert service.port() is None
    assert not cli_kernel.has_service("observability")


def test_session_fallback_without_kernel():
    set_current_kernel(None)
    from src.core.adapters.observability import DefaultObservabilityAdapter
    from src.core.session import ChatSession

    session = ChatSession()
    assert isinstance(session._observability_port, DefaultObservabilityAdapter)
