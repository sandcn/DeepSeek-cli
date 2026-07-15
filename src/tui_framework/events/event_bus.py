"""DisplayEventBus — 显示层事件总线（框架独立实现）

线程安全的事件总线，支持按事件类型过滤的发布/订阅。
使用 queue.Queue + threading.RLock 自实现线程安全，
不依赖 CoreEventBus。

对外接口与原 DisplayEventBus 保持一致：
  - subscribe(handler, event_type=None)
  - unsubscribe(handler, event_type=None)
  - publish(event)
  - get_default()
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Type

from .event_types import DisplayEvent

_logger = logging.getLogger(__name__)

EventHandler = Callable[[DisplayEvent], Any]


class DisplayEventBus:
    """显示层事件总线 — 同步发布/订阅（框架独立实现）。

    线程安全。支持按事件类型过滤订阅。
    默认单例可通过 DisplayEventBus.get_default() 获取。
    使用 threading.RLock + 内部字典实现线程安全分发，
    不依赖 CoreEventBus。
    """

    _default_instance: Optional["DisplayEventBus"] = None
    _default_lock = threading.RLock()

    def __init__(self):
        # 按类型订阅: event_type_name → list[handler]
        self._typed_handlers: Dict[str, List[EventHandler]] = {}
        # 全局订阅: 订阅所有事件
        self._global_handlers: List[EventHandler] = []
        self._lock = threading.RLock()
        self._source: str = ""

    # ── 工厂方法与默认实例 ──────────────────────────────

    @classmethod
    def get_default(cls) -> "DisplayEventBus":
        """获取全局默认事件总线实例（线程安全单例）。"""
        if cls._default_instance is None:
            with cls._default_lock:
                if cls._default_instance is None:
                    cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        """重置全局默认实例（主要用于测试）。"""
        with cls._default_lock:
            if cls._default_instance is not None:
                cls._default_instance.clear()
            cls._default_instance = None

    # ── 订阅管理 ────────────────────────────────────────

    def subscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """注册事件处理函数。

        Args:
            handler: 事件处理函数，接受 DisplayEvent 参数
            event_type: 指定订阅的事件类型。None 表示订阅所有事件。
        """
        if event_type is not None:
            if not issubclass(event_type, DisplayEvent):
                raise TypeError(f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}")
            type_name = event_type.__name__
            with self._lock:
                if type_name not in self._typed_handlers:
                    self._typed_handlers[type_name] = []
                self._typed_handlers[type_name].append(handler)
        else:
            with self._lock:
                self._global_handlers.append(handler)

    def unsubscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """移除事件处理函数。

        Args:
            handler: 之前注册的事件处理函数
            event_type: 指定取消订阅的类型。None 表示从全局订阅中移除。
        """
        with self._lock:
            if event_type is not None:
                type_name = event_type.__name__
                handlers = self._typed_handlers.get(type_name, [])
                if handler in handlers:
                    handlers.remove(handler)
                    if not handlers:
                        del self._typed_handlers[type_name]
            else:
                if handler in self._global_handlers:
                    self._global_handlers.remove(handler)

    def clear(self) -> None:
        """清除所有订阅。"""
        with self._lock:
            self._typed_handlers.clear()
            self._global_handlers.clear()

    def subscriber_count(self) -> int:
        """获取当前订阅者总数。"""
        with self._lock:
            total = len(self._global_handlers)
            for handlers in self._typed_handlers.values():
                total += len(handlers)
            return total

    # ── 发布 ────────────────────────────────────────────

    def publish(self, event: DisplayEvent) -> None:
        """同步发布事件到所有匹配的订阅者。

        Args:
            event: 要发布的事件对象
        """
        type_name = type(event).__name__

        # 收集所有匹配的 handler（在锁外调用，避免死锁）
        with self._lock:
            typed = list(self._typed_handlers.get(type_name, []))
            global_h = list(self._global_handlers)

        # 按类型订阅的 handler
        for handler in typed:
            try:
                handler(event)
            except Exception:
                _logger.exception(
                    "事件处理函数 %s 处理 %s 时异常",
                    getattr(handler, "__name__", repr(handler)),
                    type_name,
                )

        # 全局订阅的 handler
        for handler in global_h:
            try:
                handler(event)
            except Exception:
                _logger.exception(
                    "事件处理函数 %s 处理 %s 时异常",
                    getattr(handler, "__name__", repr(handler)),
                    type_name,
                )
