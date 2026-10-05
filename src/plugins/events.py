"""事件插件 — 提供 ``ctx.events``。

「一切皆插件」：核心事件总线（``core.events.event_bus``）、显示事件总线
（``core.events.display_bus``）与显示事件适配器由本服务独占持有；对应的
``get_default_bus()`` / ``DisplayEventBus.get_default()`` /
``DisplayEventBusAdapter.get_default()`` 内核优先返回服务实例，内核缺失时
才回退进程级单例，使「总线 == 内核服务」而非游离的模块级全局状态。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class EventService(Service):
    """事件服务 — 占据 ``ctx.events``。"""

    provide = "events"
    name = "events"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.events.event_bus import CoreEventBus
        from ..core.events.display_bus import DisplayEventBus

        self._bus = CoreEventBus()
        self._display_bus = DisplayEventBus()
        self._event_adapter = None

    @property
    def bus(self):
        return self._bus

    @property
    def display_bus(self):
        return self._display_bus

    def event_adapter(self, source: str = "core"):
        """返回基于本服务显示总线的默认适配器（同一实例，单例语义兼容）。"""
        if self._event_adapter is None:
            from ..core.adapters.events import DisplayEventBusAdapter

            self._event_adapter = DisplayEventBusAdapter(source=source, bus=self._display_bus)
        return self._event_adapter

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
