"""事件适配器 — EventPort 的 DisplayEventBus 实现

职责：桥接核心层 EventPort 抽象与基础设施层 DisplayEventBus。
适配器层允许导入 ui/ 模块（桥接职责）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from ..ports.events import EventPort


class DisplayEventBusAdapter(EventPort):
    """DisplayEventBus 适配器 — 实现 EventPort 接口

    将 EventPort 的调用委托给 DisplayEventBus（基础设施层）。
    作为默认回退实现，供核心模块在没有依赖注入时使用。
    """

    _default_instance: Optional["DisplayEventBusAdapter"] = None
    _default_lock = threading.RLock()

    def __init__(self, source: str = "core"):
        from ...ui.events.event_bus import DisplayEventBus
        self._bus = DisplayEventBus.get_default()
        self._source = source

    # ── 工厂方法与默认实例 ──────────────────────────────

    @classmethod
    def get_default(cls, source: str = "core") -> "DisplayEventBusAdapter":
        """获取全局默认适配器实例（线程安全单例）"""
        if cls._default_instance is None:
            with cls._default_lock:
                if cls._default_instance is None:
                    cls._default_instance = cls(source=source)
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        """重置全局默认实例（主要用于测试）"""
        with cls._default_lock:
            cls._default_instance = None

    # ── EventPort 实现 ──────────────────────────────────

    def publish(self, event_type: str, data: Any = None, source: str = "core") -> None:
        """发布字符串类型事件

        将字符串类型事件映射为对应的 DisplayEvent 并发布。
        """
        actual_source = source or self._source

        # 映射字符串事件类型到 DisplayEvent
        if event_type == "output" and isinstance(data, dict):
            text = data.get("text", "")
            level = data.get("level", "info")
            s = data.get("source", actual_source)
            self._publish_output(text, level=level, source=s)
        elif event_type == "tool_summary" and isinstance(data, dict):
            self._publish_tool_summary(data, source=actual_source)
        else:
            # 未知字符串类型，通过 publish_event 传递
            self.publish_event(data, source=actual_source)

    def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅事件（通过字符串类型名）"""
        self._bus.subscribe(handler, event_type=None)  # 订阅所有

    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """取消订阅"""
        self._bus.unsubscribe(handler, event_type=None)

    def publish_event(self, event: Any, source: str = "core") -> None:
        """发布类型化事件对象

        直接委托给 DisplayEventBus 的类型化事件发布机制。
        """
        if event is not None:
            self._bus.publish(event)

    def subscribe_type(self, event_type: type, handler: Callable) -> None:
        """按事件类型订阅"""
        self._bus.subscribe(handler, event_type=event_type)

    def unsubscribe_type(self, event_type: type, handler: Callable) -> None:
        """取消按事件类型的订阅"""
        self._bus.unsubscribe(handler, event_type=event_type)

    # ── 内部辅助方法 ────────────────────────────────────

    def _publish_output(self, text: str, level: str = "info", source: str = "core") -> None:
        """发布输出事件"""
        from ...ui.events import publish_output
        publish_output(text, level=level, source=source)

    def _publish_tool_summary(self, data: dict, source: str = "core") -> None:
        """发布工具摘要事件"""
        from ...ui.events.event_types import ToolSummaryEvent
        event = ToolSummaryEvent(
            successful_tools=tuple(data.get("successful_tools", [])),
            failed_tools=tuple(data.get("failed_tools", [])),
            source=source,
        )
        self._bus.publish(event)
