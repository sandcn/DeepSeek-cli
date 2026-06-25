"""核心事件类型定义

定义核心层通用事件类型和优先级枚举。
事件使用字符串类型标识，支持通配符订阅。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class EventPriority(IntEnum):
    """事件处理优先级（数值越大优先级越高）"""
    LOWEST = 0
    LOW = 25
    NORMAL = 50
    HIGH = 75
    HIGHEST = 100


@dataclass(frozen=True)
class CoreEvent:
    """核心事件基类

    Attributes:
        event_type: 事件类型字符串（如 "model.call.completed"）
        data: 事件负载数据
        source: 事件来源标识
        timestamp: 时间戳（秒）
        priority: 事件优先级
    """
    event_type: str
    data: dict = field(default_factory=dict)
    source: str = "core"
    timestamp: float = 0.0
    priority: EventPriority = EventPriority.NORMAL
