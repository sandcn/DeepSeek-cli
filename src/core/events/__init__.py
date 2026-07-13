"""核心事件总线 — 通用事件发布/订阅系统（已合并 CoreHooks）

历史说明：
- 此模块替代了原 `src/core/hooks.py` 中的 `CoreHooks` 类
- CoreHooks 的全部功能（on/off/_emit 回调注册、__getitem__/__contains__/__len__/copy）
  已整合到 CoreEventBus 中，接口 100% 兼容
- 旧导入 `from src.core.hooks import CoreHooks` 已不可用，请使用 `from src.core.events import CoreEventBus`

与 ui/events/ 的区别：
- ui/events/ 专用于 UI 显示层，事件类型固定为 DisplayEvent
- core/events/ 是通用事件系统，供核心模块间通信
- 两者互补，不替代彼此

端口清单（新增）:
- CoreHooks = CoreEventBus — 兼容别名（供仍使用旧名的代码过渡）

使用方式:
    from .event_bus import CoreEventBus, get_default_bus

    bus = get_default_bus()
    bus.subscribe("model.call.completed", my_handler)
    bus.publish("model.call.completed", model="deepseek", tokens=100)
"""

from .event_bus import CoreEventBus, get_default_bus, set_default_bus, reset_default_bus
from .event_types import CoreEvent, EventPriority

# CoreHooks 兼容别名（CoreHooks→CoreEventBus 合并后，仍可使用旧名引用）
CoreHooks = CoreEventBus

__all__ = [
    "CoreEventBus", "get_default_bus", "set_default_bus", "reset_default_bus",
    "CoreEvent", "EventPriority",
    # 兼容别名
    "CoreHooks",
]
