"""核心事件总线 — 通用事件发布/订阅系统

线程安全，支持通配符订阅和优先级排序。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Callable

from .event_types import CoreEvent, EventPriority

_logger = logging.getLogger(__name__)

# 处理器类型签名
EventHandler = Callable[[CoreEvent], None]


class CoreEventBus:
    """核心事件总线

    特性：
    - 线程安全（读写锁保护）
    - 通配符订阅（"model.*" 匹配 "model.call.completed"）
    - 优先级排序
    - 异常隔离（单个处理器异常不影响其他处理器）
    - 支持异步通知（通过 asyncio 事件循环）

    使用方式:
        bus = CoreEventBus()

        def on_model_call(event: CoreEvent):
            locked_print(f"模型调用完成: {event.data}")

        bus.subscribe("model.call.completed", on_model_call)
        bus.publish("model.call.completed", {"model": "deepseek", "tokens": 100})
    """

    def __init__(self):
        self._lock = threading.RLock()
        # event_type → [(priority, handler), ...] 按优先级降序
        self._handlers: dict[str, list[tuple[int, EventHandler]]] = defaultdict(list)
        self._stats: dict[str, int] = defaultdict(int)  # event_type → 发布计数

    # ── 订阅 ────────────────────────────────────────────

    def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        priority: EventPriority = EventPriority.NORMAL,
    ) -> None:
        """订阅事件

        Args:
            event_type: 事件类型（支持通配符 "*" 和 "prefix.*" 模式）
            handler: 事件处理函数
            priority: 优先级，默认为 NORMAL
        """
        with self._lock:
            handlers = self._handlers[event_type]
            handlers.append((priority.value, handler))
            # 按优先级降序排列
            handlers.sort(key=lambda x: x[0], reverse=True)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """取消订阅

        Returns:
            找到并移除返回 True，未找到返回 False
        """
        with self._lock:
            handlers = self._handlers.get(event_type)
            if not handlers:
                return False
            for i, (_, h) in enumerate(handlers):
                if h is handler:
                    handlers.pop(i)
                    return True
            return False

    # ── 发布 ────────────────────────────────────────────

    def publish(
        self,
        event_type: str,
        data: dict | None = None,
        source: str = "core",
    ) -> int:
        """发布事件

        Args:
            event_type: 事件类型
            data: 事件数据
            source: 事件来源

        Returns:
            被调用的处理器数量
        """
        event = CoreEvent(
            event_type=event_type,
            data=data or {},
            source=source,
            timestamp=time.time(),
        )
        return self._dispatch(event)

    def _dispatch(self, event: CoreEvent) -> int:
        """将事件分发给所有匹配的处理器"""
        count = 0
        with self._lock:
            self._stats[event.event_type] += 1

            # 精确匹配 — 组内已按优先级降序
            exact_matched = self._handlers.get(event.event_type, [])

            # 通配符匹配: "model.*" 匹配 "model.call.completed"
            wildcard_matched: list[tuple[int, EventHandler]] = []
            for pattern, handlers in self._handlers.items():
                if pattern.endswith("*") and not pattern.endswith("**"):
                    prefix = pattern[:-1]
                    if event.event_type.startswith(prefix):
                        wildcard_matched.extend(handlers)
                elif pattern == "*":
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
                count += 1
            except Exception:
                _logger.exception(
                    "事件处理器异常: event_type=%s handler=%s",
                    event.event_type, handler.__name__,
                )
        return count

    # ── 工具方法 ────────────────────────────────────────

    def clear(self) -> None:
        """清空所有订阅和统计"""
        with self._lock:
            self._handlers.clear()
            self._stats.clear()

    def get_stats(self) -> dict[str, int]:
        """获取各事件类型的发布计数"""
        with self._lock:
            return dict(self._stats)

    def subscriber_count(self, event_type: str | None = None) -> int:
        """获取订阅者数量"""
        with self._lock:
            if event_type:
                return len(self._handlers.get(event_type, []))
            return sum(len(h) for h in self._handlers.values())


# ── 模块级全局单例 ──────────────────────────────────────

_default_bus: CoreEventBus | None = None
_bus_lock = threading.RLock()


def get_default_bus() -> CoreEventBus:
    """获取全局默认事件总线（线程安全单例）"""
    global _default_bus
    if _default_bus is None:
        with _bus_lock:
            if _default_bus is None:
                _default_bus = CoreEventBus()
    return _default_bus


def set_default_bus(bus: CoreEventBus) -> None:
    """设置全局默认事件总线（用于测试/依赖注入）"""
    global _default_bus
    with _bus_lock:
        _default_bus = bus


def reset_default_bus() -> None:
    """重置全局默认事件总线（主要用于测试）"""
    global _default_bus
    with _bus_lock:
        _default_bus = None
