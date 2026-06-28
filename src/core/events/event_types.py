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


# ── 事件类型常量 ────────────────────────────────────────

# 模型调用
MODEL_CALL_STARTED = "model.call.started"
MODEL_CALL_COMPLETED = "model.call.completed"
MODEL_CALL_FAILED = "model.call.failed"
MODEL_STREAM_CHUNK = "model.stream.chunk"

# 工具调用
TOOL_CALL_STARTED = "tool.call.started"
TOOL_CALL_COMPLETED = "tool.call.completed"
TOOL_CALL_FAILED = "tool.call.failed"

# 会话生命周期
SESSION_STARTED = "session.started"
SESSION_COMPLETED = "session.completed"
SESSION_INTERRUPTED = "session.interrupted"
SESSION_SAVED = "session.saved"

# 上下文管理
CONTEXT_COMPRESSED = "context.compressed"
CONTEXT_COMPRESS_FAILED = "context.compress.failed"

# 配置变更
CONFIG_CHANGED = "config.changed"

# 应用生命周期
APP_BOOTSTRAP = "app.bootstrap"
APP_SHUTDOWN = "app.shutdown"
