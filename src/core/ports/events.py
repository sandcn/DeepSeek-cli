"""事件端口 — 核心层与事件总线的接口"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional
import threading


class EventPort(ABC):
    """抽象事件端口

    核心层通过此接口发布事件，UI 层订阅和消费事件。
    实现核心层与 UI 的事件解耦。
    """

    @abstractmethod
    def publish(self, event_type: str, data: Any = None, source: str = "core") -> None:
        """发布事件"""
        ...

    @abstractmethod
    def subscribe(self, event_type: str, handler) -> None:
        """订阅事件"""
        ...

    @abstractmethod
    def unsubscribe(self, event_type: str, handler) -> None:
        """取消订阅"""
        ...


class DefaultEventPort(EventPort):
    """默认事件端口 — 委托给 ui.events.DisplayEventBus。

    作为全局默认事件端口，通过延迟导入避免 core → ui 的编译期依赖。

    内部维护字符串事件类型 → DisplayEvent 类的映射，
    确保 core 层（使用字符串事件类型）与 ui 层（使用类型化事件对象）兼容。
    """

    def publish(self, event_type: str, data: Any = None, source: str = "core") -> None:
        from ...ui.events import DisplayEventBus
        bus = DisplayEventBus.get_default()

        # 将字符串事件类型映射回类型化事件对象（向后兼容 ui 层消费者）
        event_obj = self._build_event(event_type, data, source)
        if event_obj is not None:
            bus.publish(event_obj)

    def subscribe(self, event_type: str, handler) -> None:
        from ...ui.events import DisplayEventBus
        DisplayEventBus.get_default().subscribe(handler)

    def unsubscribe(self, event_type: str, handler) -> None:
        from ...ui.events import DisplayEventBus
        DisplayEventBus.get_default().unsubscribe(handler)

    # ── 字符串事件 → 类型化事件映射 ──────────────────────

    @staticmethod
    def _build_event(event_type: str, data: Any, source: str) -> Any:
        """将字符串事件类型 + data dict 转换为类型化 DisplayEvent 对象。

        延迟导入事件类，避免 core 层编译期依赖 ui.events.event_types。
        """
        if event_type == "agent_added":
            from ...ui.events.event_types import AgentAddedEvent
            return AgentAddedEvent(
                label=data.get("label", "?"),
                description=data.get("description", ""),
                status=data.get("status", "running"),
                source=source,
                dispatch_label=data.get("dispatch_label", ""),
            )
        elif event_type == "agent_status_changed":
            from ...ui.events.event_types import AgentStatusChanged
            return AgentStatusChanged(
                label=data.get("label", "?"),
                status=data.get("status", "?"),
                source=source,
            )
        # 未知事件类型：静默丢弃（安全降级）
        return None


# ── 模块级全局事件端口 ───────────────────────────────────
_default_event_port: EventPort | None = None
_event_port_lock = threading.RLock()


def get_default_event_port() -> EventPort:
    """获取全局默认事件端口（线程安全单例）"""
    global _default_event_port
    if _default_event_port is None:
        with _event_port_lock:
            if _default_event_port is None:
                _default_event_port = DefaultEventPort()
    return _default_event_port


def set_default_event_port(port: EventPort) -> None:
    """设置全局默认事件端口（用于测试/依赖注入）"""
    global _default_event_port
    with _event_port_lock:
        _default_event_port = port


def reset_default_event_port() -> None:
    """重置全局默认事件端口（主要用于测试）"""
    global _default_event_port
    with _event_port_lock:
        _default_event_port = None
