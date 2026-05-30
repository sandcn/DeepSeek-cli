"""核心事件总线 — 通用事件发布/订阅系统

与 ui/events/ 的区别：
- ui/events/ 专用于 UI 显示层，事件类型固定为 DisplayEvent
- core/events/ 是通用事件系统，供核心模块间通信
- 两者互补，不替代彼此

使用方式:
    from .event_bus import CoreEventBus, get_default_bus

    bus = get_default_bus()
    bus.subscribe("model.call.completed", my_handler)
    bus.publish("model.call.completed", model="deepseek", tokens=100)
"""

from .event_bus import CoreEventBus, get_default_bus, set_default_bus, reset_default_bus
from .event_types import CoreEvent, EventPriority

__all__ = [
    "CoreEventBus", "get_default_bus", "set_default_bus", "reset_default_bus",
    "CoreEvent", "EventPriority",
]
