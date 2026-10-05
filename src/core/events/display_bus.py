"""DisplayEventBus — 显示层事件总线（直接分发实现）。

按事件类型（DisplayEvent 子类）存储 handler 列表，publish() 直接分发，
异常隔离 + 按事件类型限频日志。线程安全（RLock 保护注册表）。

架构位置：核心层（由原 ``tui/events/event_bus.py`` 下沉而来），表现层
``tui.events.event_bus`` 仅 re-export 兼容。基础设施层（api/tools）可经此
发布显示事件，不再反向依赖表现层。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional, Type

from .display_types import DisplayEvent
from ..singleton import SingletonMeta

_logger = logging.getLogger(__name__)

_EXC_LOG_WINDOW = 5.0
_last_exc_log: dict[str, float] = {}

EventHandler = Callable[[DisplayEvent], Any]


class DisplayEventBus(metaclass=SingletonMeta):
    """显示层事件总线 — 同步发布/订阅（直接分发实现）。

    支持直接构造独立实例（测试/多实例隔离）；``get_default()`` 内核优先返回
    ``ctx.events`` 服务独占实例，内核缺失时返回进程级默认实例。
    """

    def __init__(self):
        if getattr(self, "_handlers", None) is not None:
            return
        self._handlers: dict[type, list[EventHandler]] = {}
        self._all_handlers: list[EventHandler] = []
        self._lock = threading.RLock()

    @classmethod
    def get_default(cls) -> "DisplayEventBus":
        """获取默认显示事件总线。

        内核优先：内核挂载 ``ctx.events`` 服务后返回其独占的总线（与内核
        事件服务同源）；内核缺失或服务尚在构造中时回退进程级单例。
        """
        try:
            from ...kernel.runtime import active_service

            service = active_service("events")
            bus = getattr(service, "display_bus", None) if service is not None else None
            if bus is not None:
                return bus
        except Exception:
            pass
        return SingletonMeta.get_default(cls)

    def subscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """注册事件处理函数；event_type=None 表示订阅所有事件。"""
        if event_type is not None:
            if not isinstance(event_type, type) or not issubclass(event_type, DisplayEvent):
                raise TypeError(f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}")
            with self._lock:
                handlers = self._handlers.setdefault(event_type, [])
                if handler not in handlers:
                    handlers.append(handler)
        else:
            with self._lock:
                if handler not in self._all_handlers:
                    self._all_handlers.append(handler)

    def unsubscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """移除事件处理函数。"""
        if event_type is not None:
            if not isinstance(event_type, type) or not issubclass(event_type, DisplayEvent):
                raise TypeError(f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}")
        with self._lock:
            if event_type is not None:
                handlers = self._handlers.get(event_type)
                if handlers and handler in handlers:
                    handlers.remove(handler)
                    if not handlers:
                        del self._handlers[event_type]
            else:
                if handler in self._all_handlers:
                    self._all_handlers.remove(handler)

    def clear(self) -> None:
        """清除所有订阅（全量重置，供测试隔离与整体 teardown）。"""
        with self._lock:
            self._handlers.clear()
            self._all_handlers.clear()

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            count = len(self._all_handlers)
            for handlers in self._handlers.values():
                count += len(handlers)
            return count

    def publish(self, event: DisplayEvent) -> None:
        """同步发布事件到所有匹配的订阅者（锁内快照，锁外调用）。"""
        event_type = type(event)
        targets: list[EventHandler] = []
        with self._lock:
            if event_type in self._handlers:
                targets.extend(self._handlers[event_type])
            targets.extend(self._all_handlers)
        for handler in targets:
            try:
                handler(event)
            except Exception:
                handler_name = getattr(handler, "__name__", repr(handler))
                etype_name = event_type.__name__
                now = time.monotonic()
                if now - _last_exc_log.get(etype_name, 0.0) >= _EXC_LOG_WINDOW:
                    _last_exc_log[etype_name] = now
                    _logger.warning(
                        "事件处理函数 %s 处理 %s 时异常（5s 限频）",
                        handler_name, etype_name, exc_info=True,
                    )
                else:
                    _logger.debug(
                        "事件处理函数 %s 处理 %s 时异常（限频抑制）",
                        handler_name, etype_name,
                    )


__all__ = ["DisplayEventBus", "EventHandler"]
