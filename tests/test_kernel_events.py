"""内核事件系统测试 — 五种分发模式与监听器生命周期。"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, plugin
from src.kernel.events import EventBus


@pytest.fixture
def bus():
    return EventBus()


async def test_emit_calls_all_sync_listeners(bus):
    seen = []
    bus.on("e", lambda v: seen.append(v))
    bus.on("e", lambda v: seen.append(v + 1))
    await bus.emit("e", 1)
    assert seen == [1, 2]


async def test_emit_ignores_return_values(bus):
    async def handler(v):
        return v

    bus.on("e", handler)
    assert await bus.emit("e", 5) is None


async def test_parallel_runs_all(bus):
    seen = []

    async def slow(v):
        seen.append(v)

    bus.on("p", slow)
    bus.on("p", slow)
    await bus.parallel("p", "x")
    assert seen == ["x", "x"]


async def test_serial_returns_last(bus):
    bus.on("s", lambda v: v + 1)
    bus.on("s", lambda v: v + 2)
    assert await bus.serial("s", 10) == 12


async def test_bail_stops_at_first_non_none(bus):
    calls = []
    bus.on("b", lambda v: calls.append("a"))
    bus.on("b", lambda v: "stop")
    bus.on("b", lambda v: calls.append("c"))
    assert await bus.bail("b", 0) == "stop"
    assert calls == ["a"]


async def test_bail_returns_none_when_no_handler_bails(bus):
    bus.on("b", lambda v: None)
    assert await bus.bail("b", 0) is None


async def test_waterfall_wraps_result(bus):
    async def wrapper(*args):
        nxt = args[-1]
        inner = await nxt()
        return f"[{inner}]"

    bus.on("w", wrapper)
    bus.on("w", lambda *args: "core")
    assert await bus.waterfall("w") == "[core]"


async def test_waterfall_short_circuit(bus):
    bus.on("w", lambda *args: "short")
    bus.on("w", lambda *args: "never")
    assert await bus.waterfall("w") == "short"


async def test_prepend_runs_first(bus):
    order = []
    bus.on("e", lambda: order.append("normal"))
    bus.on("e", lambda: order.append("prepend"), prepend=True)
    await bus.emit("e")
    assert order == ["prepend", "normal"]


async def test_priority_ordering(bus):
    order = []
    bus.on("e", lambda: order.append("low"), priority=1)
    bus.on("e", lambda: order.append("high"), priority=10)
    await bus.emit("e")
    assert order == ["high", "low"]


async def test_disposer_removes_listener(bus):
    seen = []
    dispose = bus.on("e", lambda: seen.append(1))
    dispose()
    await bus.emit("e")
    assert seen == []
    assert bus.listener_count("e") == 0


async def test_context_on_disposed_with_fiber():
    kernel = Kernel()

    @plugin("listener")
    def apply(ctx):
        ctx.on("evt", lambda: None)

    fiber = kernel.plugin(apply)
    await kernel.settle()
    assert kernel.bus.listener_count("evt") == 1
    await fiber.dispose()
    assert kernel.bus.listener_count("evt") == 0


async def test_context_dispatch_methods():
    kernel = Kernel()
    seen = []
    kernel.root.on("e", lambda v: seen.append(v))
    await kernel.root.emit("e", 7)
    assert seen == [7]
    assert await kernel.root.serial("e", 8) is None
    assert await kernel.root.bail("missing", 1) is None
