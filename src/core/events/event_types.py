"""核心事件类型定义

定义核心层通用事件类型和优先级枚举。
事件使用字符串类型标识，支持通配符订阅。

「一切皆插件」：16 个核心事件类型字符串常量不再是本模块的硬编码字面量，而是
登记到 ``type_registry``（域 ``core``）——由清单中的独立插件条目
（``event_type``，经 ``src.plugins.event_type_entries``）注册，可按 Profile/
Patch/Overlay 覆盖或禁用。常量经模块级 ``__getattr__`` 实时解析
（``event_types.MODEL_CALL_STARTED``）；``from ... import MODEL_CALL_STARTED``
在导入时解析为当前值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from .type_registry import declare_events, event_value


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


# ── 事件类型常量（登记到 type_registry，域 core） ─────────

#: 内置核心事件类型声明（常量名 → 事件类型字符串）。
_CORE_EVENT_TYPES: dict = {
    # 模型调用
    "MODEL_CALL_STARTED": "model.call.started",
    "MODEL_CALL_COMPLETED": "model.call.completed",
    "MODEL_CALL_FAILED": "model.call.failed",
    "MODEL_STREAM_CHUNK": "model.stream.chunk",
    # 工具调用
    "TOOL_CALL_STARTED": "tool.call.started",
    "TOOL_CALL_COMPLETED": "tool.call.completed",
    "TOOL_CALL_FAILED": "tool.call.failed",
    # 会话生命周期
    "SESSION_STARTED": "session.started",
    "SESSION_COMPLETED": "session.completed",
    "SESSION_INTERRUPTED": "session.interrupted",
    "SESSION_SAVED": "session.saved",
    # 上下文管理
    "CONTEXT_COMPRESSED": "context.compressed",
    "CONTEXT_COMPRESS_FAILED": "context.compress.failed",
    # 配置变更
    "CONFIG_CHANGED": "config.changed",
    # 应用生命周期
    "APP_BOOTSTRAP": "app.bootstrap",
    "APP_SHUTDOWN": "app.shutdown",
}

declare_events("core", _CORE_EVENT_TYPES)


def __getattr__(name: str):
    """实时解析核心事件类型常量（注册表未登记/被禁用时抛 AttributeError）。"""
    if name in _CORE_EVENT_TYPES:
        value = event_value("core", name, None)
        if value is not None:
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["CoreEvent", "EventPriority", *_CORE_EVENT_TYPES]
