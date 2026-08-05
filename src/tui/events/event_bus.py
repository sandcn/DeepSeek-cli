"""DisplayEventBus — 显示层事件总线（直接分发实现）。

自行实现线程安全的事件分发，移除对 CoreEventBus 的包装委托，
消除高频事件（ContentChunkEvent/ReasoningChunkEvent）的装箱/拆箱开销。

架构：
  - 按事件类型（DisplayEvent 子类）存储 handler 列表
  - subscribe() 支持按类型订阅或订阅所有（event_type=None）
  - publish() 直接调用 handler，异常隔离

设计原则：
  - 线程安全：RLock 保护 handler 注册表，异常隔离
  - 轻量无依赖：无需 CoreEventBus，自实现完整分发
  - 向后兼容：所有公开接口签名与旧版完全一致

2026-08-05 死代码清理：时间窗口批处理机制（``_TimeWindowBatcher`` /
``register_batched_event`` / ``unregister_batched_event`` / ``_batched_events``）
已删除——生产路径从未启用（批处理将「延迟分发的高频事件」与「同步直发的
阶段切换事件」的顺序竞态放大为固定窗口，导致推理文本静默丢失），保留属于
未启用死代码。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional, Type

from .event_types import DisplayEvent
from ..core.singleton import SingletonMeta

_logger = logging.getLogger(__name__)

EventHandler = Callable[[DisplayEvent], Any]


# ═══════════════════════════════════════════════════════════
# DisplayEventBus — 显示层事件总线
# ═══════════════════════════════════════════════════════════

class DisplayEventBus(metaclass=SingletonMeta):
    """显示层事件总线 — 同步发布/订阅（直接分发实现）。

    线程安全。支持按事件类型过滤订阅。
    单例行为由 ``SingletonMeta`` 自动提供 get_default / reset_default。

    单例作用域评估（2026-07-31 方向D）：
      CLI/WebUI 共享 DisplayEventBus 进程级单例为既有架构；webui bridge
      （_EVENT_BINDINGS 16 类）与 TUI 内部（_lifecycle/_subagent_panel/consumers）
      强依赖 get_default()，隔离改动过大 → 标记 P2 遗留，保持单例，不做隔离。
    """

    def __init__(self):
        self._handlers: dict[type, list[EventHandler]] = {}
        self._all_handlers: list[EventHandler] = []
        self._lock = threading.RLock()

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
        """清除所有订阅（全量重置，供测试隔离与整体 teardown）。

        与 stop() 不同（stop 不注销批处理以保持生命周期内稳定），
        clear() 为完整重置语义。批处理机制已随 2026-08-05 死代码清理移除。

        P2-7 审计：clear() 全量重置（订阅）仅供测试隔离/整体 teardown；
        **生产代码当前无调用方**（生产路径使用 subscribe/unsubscribe
        或 stop() 的生命周期语义）。
        """
        with self._lock:
            self._handlers.clear()
            self._all_handlers.clear()

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
        for handler in targets:
            try:
                handler(event)
            except Exception:
                _logger.exception(
                    "事件处理函数 %s 处理 %s 时异常",
                    getattr(handler, "__name__", repr(handler)),
                    event_type.__name__,
                )
