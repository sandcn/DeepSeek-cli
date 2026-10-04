"""事件插件 — 提供 ``ctx.events``。

包装核心事件总线（``core.events.event_bus``）、显示事件总线
（``core.events.display_bus``）与发布工具（``core.events.publish``）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class EventService(Service):
    """事件服务 — 占据 ``ctx.events``。"""

    provide = "events"
    name = "events"

    @property
    def bus(self):
        from ..core.events.event_bus import get_default_bus

        return get_default_bus()

    @property
    def display_bus(self):
        from ..core.events.display_bus import DisplayEventBus

        return DisplayEventBus.get_default()

    def publish(self, event_type: str, data: dict | None = None, source: str = "core") -> int:
        return self.bus.publish(event_type, data, source)

    def subscribe(self, event_type: str, handler, priority=None):
        if priority is None:
            return self.bus.subscribe(event_type, handler)
        return self.bus.subscribe(event_type, handler, priority)

    def unsubscribe(self, event_type: str, handler) -> bool:
        return self.bus.unsubscribe(event_type, handler)

    def emit(self, event) -> None:
        from ..core.events.publish import emit

        emit(event, bus=self.display_bus)

    def subscribe_display(self, handler, event_type=None):
        return self.display_bus.subscribe(handler, event_type=event_type)


@plugin("events", provide=["events"])
def apply(ctx):
    return EventService(ctx)
