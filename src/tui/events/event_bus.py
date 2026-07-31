"""DisplayEventBus — 显示层事件总线（直接分发实现）。

自行实现线程安全的事件分发，移除对 CoreEventBus 的包装委托，
消除高频事件（ContentChunkEvent/ReasoningChunkEvent）的装箱/拆箱开销。

架构：
  - 按事件类型（DisplayEvent 子类）存储 handler 列表
  - subscribe() 支持按类型订阅或订阅所有（event_type=None）
  - publish() 直接调用 handler，异常隔离
  - 批处理机制：高频事件 ~33ms 时间窗口合并

设计原则：
  - 线程安全：RLock 保护 handler 注册表，异常隔离
  - 轻量无依赖：无需 CoreEventBus，自实现完整分发
  - 向后兼容：所有公开接口签名与旧版完全一致
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional, Type

from .event_types import DisplayEvent
from ..core.singleton import SingletonMeta

_logger = logging.getLogger(__name__)

EventHandler = Callable[[DisplayEvent], Any]


# ═══════════════════════════════════════════════════════════
# _TimeWindowBatcher — 时间窗口批处理器
# ═══════════════════════════════════════════════════════════

class _TimeWindowBatcher:
    """时间窗口批处理器 — 在指定时间窗口内合并对同一 handler 的多次触发。

    用于高频事件（ContentChunkEvent, ReasoningChunkEvent）的批处理，
    减少渲染压力。窗口默认 ~33ms。
    """

    def __init__(self, window: float = 0.033):
        self._window = window
        self._last_dispatch: float = 0.0
        self._pending: list[tuple[EventHandler, DisplayEvent]] = []
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None

    def enqueue(self, handler: EventHandler, event: DisplayEvent) -> None:
        """将事件加入待处理队列，在时间窗口结束后统一分发。"""
        with self._lock:
            self._pending.append((handler, event))
            now = time.monotonic()
            if now - self._last_dispatch >= self._window:
                self._flush()
            elif self._timer is None:
                remaining = self._window - (now - self._last_dispatch)
                self._timer = threading.Timer(remaining, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if not self._pending:
                return
            batch = self._pending[:]
            self._pending.clear()
            self._last_dispatch = time.monotonic()
        for handler, event in batch:
            try:
                handler(event)
            except Exception:
                _logger.exception(
                    "批处理事件处理函数 %s 处理 %s 时异常",
                    getattr(handler, "__name__", repr(handler)),
                    type(event).__name__,
                )

    def clear(self) -> None:
        """清空待处理队列。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending.clear()


# ═══════════════════════════════════════════════════════════
# DisplayEventBus — 显示层事件总线
# ═══════════════════════════════════════════════════════════

class DisplayEventBus(metaclass=SingletonMeta):
    """显示层事件总线 — 同步发布/订阅（直接分发实现）。

    线程安全。支持按事件类型过滤订阅。
    单例行为由 ``SingletonMeta`` 自动提供 get_default / reset_default。
    """

    def __init__(self):
        self._handlers: dict[type, list[EventHandler]] = {}
        self._all_handlers: list[EventHandler] = []
        self._lock = threading.RLock()
        self._batched_events: set[type] = set()
        self._batcher = _TimeWindowBatcher()
        self._source: str = ""

    # 单例访问由 SingletonMeta 提供：
    #   DisplayEventBus.get_default() → 线程安全单例获取（DCL）
    #   DisplayEventBus.reset_default() → 线程安全单例重置（供测试使用）

    # ── 订阅管理 ────────────────────────────────────────

    def subscribe(
        self,
        handler: EventHandler,
        event_type: Optional[Type[DisplayEvent]] = None,
    ) -> None:
        """注册事件处理函数。

        Args:
            handler: 事件处理函数，接受 DisplayEvent 参数。
            event_type: 指定订阅的事件类型。None 表示订阅所有事件。
        """
        if event_type is not None:
            if not issubclass(event_type, DisplayEvent):
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
        """移除事件处理函数。

        Args:
            handler: 之前注册的事件处理函数。
            event_type: 指定取消订阅的类型。None 表示从全局订阅中移除。
        """
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
        """清除所有订阅。"""
        with self._lock:
            self._handlers.clear()
            self._all_handlers.clear()
            self._batcher.clear()

    @property
    def subscriber_count(self) -> int:
        """获取当前订阅者总数。"""
        with self._lock:
            count = len(self._all_handlers)
            for handlers in self._handlers.values():
                count += len(handlers)
            return count

    # ── 发布 ────────────────────────────────────────────

    def publish(self, event: DisplayEvent) -> None:
        """同步发布事件到所有匹配的订阅者。

        Args:
            event: 要发布的事件对象。
        """
        event_type = type(event)
        # 收集目标 handler（在锁内快照，锁外调用避免死锁）
        targets: list[EventHandler] = []
        with self._lock:
            if event_type in self._handlers:
                targets.extend(self._handlers[event_type])
            targets.extend(self._all_handlers)
        if not targets:
            return
        # 批处理检查
        if event_type in self._batched_events:
            for handler in targets:
                self._batcher.enqueue(handler, event)
        else:
            for handler in targets:
                try:
                    handler(event)
                except Exception:
                    _logger.exception(
                        "事件处理函数 %s 处理 %s 时异常",
                        getattr(handler, "__name__", repr(handler)),
                        event_type.__name__,
                    )

    # ── 时间窗口批处理 ──────────────────────────────────

    def register_batched_event(self, event_type: type[DisplayEvent]) -> None:
        """注册需要时间窗口批处理的事件类型。

        高频 UI 事件（如 ContentChunkEvent、ReasoningChunkEvent）
        走 ~33ms 窗口批处理，降低渲染压力。

        Args:
            event_type: DisplayEvent 的子类。
        """
        if not issubclass(event_type, DisplayEvent):
            raise TypeError(
                f"event_type 必须是 DisplayEvent 的子类，收到: {event_type}"
            )
        with self._lock:
            self._batched_events.add(event_type)

    def unregister_batched_event(self, event_type: type[DisplayEvent]) -> None:
        """取消事件类型的批处理注册。

        Args:
            event_type: DisplayEvent 的子类。
        """
        with self._lock:
            self._batched_events.discard(event_type)
