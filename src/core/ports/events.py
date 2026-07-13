"""事件端口 — 核心层与事件总线的接口"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Callable, Optional, TypeVar, Generic

T = TypeVar("T")


class EventPort(ABC):
    """抽象事件端口

    核心层通过此接口发布事件，UI 层订阅和消费事件。
    实现核心层与 UI 的事件解耦。

    支持两种事件发布模式：
    1. 字符串类型事件（旧 API）：publish(event_type, data, source) — 兼容已有事件系统
    2. 类型化事件（新 API）：publish_event(event, source) — 通过类型化事件对象发布
    """

    @abstractmethod
    def publish(self, event_type: str, data: Any = None, source: str = "core") -> None:
        """发布字符串类型事件（向后兼容）

        Args:
            event_type: 事件类型字符串（如 "output", "tool_summary"）
            data: 事件数据
            source: 事件来源标识
        """
        ...

    @abstractmethod
    def subscribe(self, event_type: str, handler: Callable) -> None:
        """订阅事件（通过字符串类型名）

        当 event_type 为具体字符串时，仅接收匹配该类型的事件；
        当 event_type 为 None 或空字符串时，接收所有事件（向后兼容）。

        匹配规则：先检查事件对象的 event_type 属性，若匹配则触发；
                 否则按事件对象的类名匹配。

        与 subscribe_type() 行为差异：
        - subscribe("OutputEvent", handler) — 按字符串名过滤，接收 OutputEvent 类事件
        - subscribe_type(OutputEvent, handler) — 按类型对象订阅，接收 OutputEvent 类事件
        两者使用不同的过滤维度，结果可能相同但机制不同。

        Args:
            event_type: 事件类型字符串（如 "OutputEvent", "ToolSummaryEvent"）。
                       None 或空表示订阅所有事件。
            handler: 事件处理函数
        """
        ...

    @abstractmethod
    def unsubscribe(self, event_type: str, handler: Callable) -> None:
        """取消订阅"""
        ...

    # ── 类型化事件 API（新） ──────────────────────────

    @abstractmethod
    def publish_event(self, event: Any, source: str = "core") -> None:
        """发布类型化事件对象

        接受任意类型化事件对象（如 ToolStartedEvent, AgentAddedEvent 等），
        直接委托给事件总线的类型化事件发布机制。

        Args:
            event: 类型化事件对象（frozen dataclass 实例）
            source: 事件来源标识
        """
        ...

    @abstractmethod
    def subscribe_type(self, event_type: type, handler: Callable) -> None:
        """按事件类型订阅

        Args:
            event_type: 事件类型（类本身，如 ToolStartedEvent）
            handler: 订阅处理器
        """
        ...

    @abstractmethod
    def unsubscribe_type(self, event_type: type, handler: Callable) -> None:
        """取消按事件类型的订阅"""
        ...
