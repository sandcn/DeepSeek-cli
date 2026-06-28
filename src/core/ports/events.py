"""事件端口 — 核心层与事件总线的接口"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Optional


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
