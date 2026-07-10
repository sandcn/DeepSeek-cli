"""DisplayEventBus — 显示层事件总线（基于 CoreEventBus 实现）

内部委托给 CoreEventBus 实现线程安全的事件分发，
消除与核心层事件总线的功能重叠。

对外接口完全不变。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional, Type

from .event_types import DisplayEvent
from ...core.events.event_bus import CoreEventBus
from ...core.events.event_types import CoreEvent, EventPriority

_logger = logging.getLogger(__name__)

EventHandler = Callable[[DisplayEvent], Any]


class DisplayEventBus:
    """显示层事件总线 — 同步发布/订阅（基于 CoreEventBus 实现）。

    线程安全。支持按事件类型过滤订阅。
    默认单例可通过 DisplayEventBus.get_default() 获取。
    内部委托给 CoreEventBus 进行事件分发。
    """

    _default_instance: Optional["DisplayEventBus"] = None
    _default_lock = threading.RLock()

    def __init__(self):
        # 内部委托给 CoreEventBus（复用线程安全分发+异常隔离）
        self._bus = CoreEventBus()
        # 维护 handler 映射: original_handler → list of (mode, event_type, wrapper)
        # mode: 'type' 表示按类型订阅, 'all' 表示订阅所有事件
        # ★ 改为列表存储，支持同一 handler 在多个 event_type 上注册
        self._handler_map: Dict[EventHandler, list[tuple[str, Optional[type], Any]]] = {}
        self._handler_lock = threading.RLock()
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
            # 创建包装器：CoreEvent → 提取 DisplayEvent → 调用原始 handler
            wrapper = self._make_wrapper(handler)
            self._bus.subscribe(event_type.__name__, wrapper, priority=EventPriority.NORMAL)
            with self._handler_lock:
                entries = self._handler_map.setdefault(handler, [])
                entries.append(('type', event_type, wrapper))
        else:
            wrapper = self._make_wrapper(handler)
            self._bus.subscribe('*', wrapper, priority=EventPriority.NORMAL)
            with self._handler_lock:
                entries = self._handler_map.setdefault(handler, [])
                entries.append(('all', None, wrapper))

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
        with self._handler_lock:
            entries = self._handler_map.get(handler)
            if not entries:
                return
            # 找到匹配的条目移除
            for i, (mode, et, wrapper) in enumerate(entries):
                if (event_type is None and mode == 'all') or \
                   (event_type is not None and mode == 'type' and et == event_type):
                    entry = entries.pop(i)
                    _mode, _et, _wrapper = entry  # 锁内提取到局部变量
                    if not entries:
                        del self._handler_map[handler]
                    break
            else:
                return  # 无匹配条目
        mode, et, wrapper = _mode, _et, _wrapper  # 锁外使用局部变量
        if mode == 'type' and et is not None:
            self._bus.unsubscribe(et.__name__, wrapper)
        else:
            self._bus.unsubscribe('*', wrapper)

    def clear(self) -> None:
        """清除所有订阅。"""
        self._bus.clear()
        with self._handler_lock:
            self._handler_map.clear()

    @property
    def subscriber_count(self) -> int:
        """获取当前订阅者总数。"""
        return self._bus.subscriber_count()

    # ── 发布 ────────────────────────────────────────────

    def publish(self, event: DisplayEvent) -> None:
        """同步发布事件到所有匹配的订阅者。

        Args:
            event: 要发布的事件对象
        """
        # 将 DisplayEvent 包装为 CoreEvent 发布
        event_type_name = type(event).__name__
        self._bus.publish(
            event_type=event_type_name,
            data={'_display_event': event},
            source=event.source or self._source,
        )

    # ── 内部方法 ────────────────────────────────────────

    @staticmethod
    def _make_wrapper(handler: EventHandler) -> Callable[[CoreEvent], None]:
        """创建 CoreEvent → DisplayEvent 的适配包装器

        从 CoreEvent.data['_display_event'] 中提取原始 DisplayEvent，
        再调用原始 handler。

        Args:
            handler: 原始 DisplayEvent handler

        Returns:
            适配后的 CoreEvent handler
        """
        def wrapper(core_event: CoreEvent) -> None:
            display_event = core_event.data.get('_display_event')
            if display_event is None:
                return
            try:
                handler(display_event)
            except Exception:
                _logger.exception(
                    "事件处理函数 %s 处理 %s 时异常",
                    getattr(handler, "__name__", repr(handler)),
                    type(display_event).__name__,
                )
        return wrapper
