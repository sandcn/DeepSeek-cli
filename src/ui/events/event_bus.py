"""DisplayEventBus — 显示层事件总线（自包含线程安全实现）

直接在 DisplayEventBus 内实现线程安全的事件分发（精确匹配 + 通配符匹配），
不再委托给 CoreEventBus，消除三层委托结构（CoreEventBus → DisplayEventBus → EventDispatcher）。

对外接口完全不变。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable, Dict, Optional, Type

from .event_types import DisplayEvent

_logger = logging.getLogger(__name__)

EventHandler = Callable[[DisplayEvent], Any]

# 默认优先级值（与原 CoreEventBus.EventPriority.NORMAL = 50 保持一致）
_DEFAULT_PRIORITY = 50


class DisplayEventBus:
    """显示层事件总线 — 同步发布/订阅（自包含线程安全实现）。

    线程安全。支持按事件类型过滤订阅。
    默认单例可通过 DisplayEventBus.get_default() 获取。

    特性：
    - 线程安全（RLock 保护 _handlers / _stats）
    - 通配符订阅（"*" 匹配所有事件类型）
    - 优先级排序（按优先级降序分发）
    - 异常隔离（单个处理器异常不影响其他处理器）
    """

    _default_instance: Optional["DisplayEventBus"] = None
    _default_lock = threading.RLock()

    def __init__(self):
        self._lock = threading.RLock()
        # event_type_name → [(priority, handler), ...] 按优先级降序
        self._handlers: dict[str, list[tuple[int, EventHandler]]] = defaultdict(list)
        self._stats: dict[str, int] = defaultdict(int)  # event_type → 发布计数
        # 记录每个 handler 的订阅信息，用于 unsubscribe 精确移除
        # handler → [(mode, event_type), ...]
        #   mode: 'type' = 按事件类型订阅, 'all' = 订阅所有事件
        self._handler_map: Dict[EventHandler, list[tuple[str, Optional[Type[DisplayEvent]]]]] = {}
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
            event_type: 指定订阅的事件类型。None 表示订阅所有事件（通配符 "*"）。
        """
        if event_type is not None:
            if not issubclass(event_type, DisplayEvent):
                raise TypeError(f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}")
            event_type_str = event_type.__name__
            with self._lock:
                handlers = self._handlers[event_type_str]
                handlers.append((_DEFAULT_PRIORITY, handler))
                # 按优先级降序排列
                handlers.sort(key=lambda x: x[0], reverse=True)
            with self._handler_lock:
                entries = self._handler_map.setdefault(handler, [])
                entries.append(('type', event_type))
        else:
            with self._lock:
                handlers = self._handlers['*']
                handlers.append((_DEFAULT_PRIORITY, handler))
                handlers.sort(key=lambda x: x[0], reverse=True)
            with self._handler_lock:
                entries = self._handler_map.setdefault(handler, [])
                entries.append(('all', None))

    def unsubscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """移除事件处理函数。

        Args:
            handler: 之前注册的事件处理函数
            event_type: 指定取消订阅的类型。None 表示从全局订阅（"*"）中移除。
        """
        # 第一步：从 _handler_map 中查找并移除记录
        with self._handler_lock:
            entries = self._handler_map.get(handler)
            if not entries:
                return
            # 找到匹配的条目移除
            for i, (mode, et) in enumerate(entries):
                if (event_type is None and mode == 'all') or \
                   (event_type is not None and mode == 'type' and et == event_type):
                    entry = entries.pop(i)
                    if not entries:
                        del self._handler_map[handler]
                    break
            else:
                return  # 无匹配条目

        # 第二步：从 _handlers 中移除 handler
        mode, et = entry
        event_type_str = '*' if mode == 'all' else et.__name__
        with self._lock:
            handlers = self._handlers.get(event_type_str)
            if not handlers:
                return
            for i, (_, h) in enumerate(handlers):
                if h == handler:
                    handlers.pop(i)
                    if not handlers:
                        # 清理空列表以保持 subscriber_count 准确
                        del self._handlers[event_type_str]
                    return

    def clear(self) -> None:
        """清除所有订阅和统计。"""
        with self._lock:
            self._handlers.clear()
            self._stats.clear()
        with self._handler_lock:
            self._handler_map.clear()

    @property
    def subscriber_count(self) -> int:
        """获取当前订阅者总数。"""
        with self._lock:
            return sum(len(h) for h in self._handlers.values())

    # ── 发布 ────────────────────────────────────────────

    def publish(self, event: DisplayEvent) -> None:
        """同步发布事件到所有匹配的订阅者。

        Args:
            event: 要发布的事件对象
        """
        event_type_name = type(event).__name__
        self._dispatch(event_type_name, event)

    def _dispatch(self, event_type_name: str, event: DisplayEvent) -> None:
        """将事件分发给所有匹配的处理器（线程安全，异常隔离）。

        分发策略（与 CoreEventBus._dispatch 等效）：
        1. 精确匹配：按事件类型名精确查找 handlers
        2. 通配符匹配：查找前缀通配符（"prefix.*"）和全通配符（"*"）
        3. 去重：精确匹配优先于通配符匹配（首现保留原则）
        4. 锁外分发：在释放锁后调用 handler，避免死锁
        5. 异常隔离：单个 handler 异常不影响其他 handler 和总线状态
        """
        with self._lock:
            self._stats[event_type_name] += 1

            # 精确匹配 — 组内已按优先级降序
            exact_matched = self._handlers.get(event_type_name, [])

            # 通配符匹配: "prefix.*" 匹配 "prefix.something"
            wildcard_matched: list[tuple[int, EventHandler]] = []
            for pattern, handlers in self._handlers.items():
                if pattern.endswith('*') and not pattern.endswith('**'):
                    prefix = pattern[:-1]
                    if event_type_name.startswith(prefix):
                        wildcard_matched.extend(handlers)
                elif pattern == '*':
                    wildcard_matched.extend(handlers)

            # 通配符全局按优先级降序
            wildcard_matched.sort(key=lambda x: x[0], reverse=True)

            # 去重：精确优先于通配符（首现保留原则）
            seen: set[EventHandler] = set()
            unique_handlers: list[EventHandler] = []
            for _, h in exact_matched:
                if h not in seen:
                    seen.add(h)
                    unique_handlers.append(h)
            for _, h in wildcard_matched:
                if h not in seen:
                    seen.add(h)
                    unique_handlers.append(h)

        # 在锁外分发，避免处理器死锁
        for handler in unique_handlers:
            try:
                handler(event)
            except Exception:
                _logger.exception(
                    "事件处理器异常: event_type=%s handler=%s",
                    event_type_name,
                    getattr(handler, "__name__", repr(handler)),
                )

    # ── 工具方法 ────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """获取各事件类型的发布计数。"""
        with self._lock:
            return dict(self._stats)
