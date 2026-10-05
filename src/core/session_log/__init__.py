"""会话日志子系统 — 仅追加事件日志、消息视图与投影（会话的事实源）。

- ``SessionLog``：仅追加的会话事件日志；
- ``LoggedMessageList``：由日志驱动的消息列表视图（事实源在日志侧）；
- ``derive_messages`` / ``ProjectionRegistry``：从事件派生模型历史与
  类型化状态；
- ``SessionEvent``：不可变事件记录。
"""

from __future__ import annotations

from .builtin_projections import (
    BUILTIN_PROJECTIONS,
    builtin_projection_names,
    builtin_projection_spec,
)
from .events import SessionEvent, message_event_type, message_to_event_data
from .log import SessionLog
from .projection import MessageProjection, ProjectionRegistry, StateFolder, derive_messages
from .view import LoggedMessageList

__all__ = [
    "SessionEvent",
    "SessionLog",
    "LoggedMessageList",
    "derive_messages",
    "ProjectionRegistry",
    "MessageProjection",
    "StateFolder",
    "message_event_type",
    "message_to_event_data",
    "BUILTIN_PROJECTIONS",
    "builtin_projection_names",
    "builtin_projection_spec",
]
