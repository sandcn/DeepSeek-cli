"""会话内部实现 — 消息管理、状态管理、持久化、压缩"""
from __future__ import annotations

from ._session_messages import *
from ._session_state import *
from ._session_compression import *
from ._session_persistence import *
from ._session_persistence_manager import *
from ._session_messaging_manager import *
from ._session_compression import _validate_compress_preconditions  # noqa: E402 — _ 前缀，显式导入

__all__ = [
    # _session_messages
    "add_message", "non_system_messages", "system_messages",
    # _session_state
    "CoreHooks", "SessionState",
    # _session_compression
    "_validate_compress_preconditions",
    # _session_persistence
    "save_session", "load_session_data",
    "save_checkpoint_session", "clear_checkpoint_session",
    "has_checkpoint_session", "resume_from_checkpoint_session",
    "load_checkpoint_data", "safe_save_state",
    "get_session_ids_fn", "list_sessions_fn",
    # _session_persistence_manager
    "SessionPersistenceManager",
    # _session_messaging_manager
    "SessionMessagingManager",
]
