"""会话内部实现 — 消息管理、状态管理、持久化、压缩、生命周期编排

子模块:
- _session_lifecycle       — 生命周期编排（run_round/retry/run_single 等，从 session.py 提取）
- _session_messages        — 消息操作函数
- _session_state           — 会话可变状态容器与 CoreHooks
- _session_compression     — 压缩前置条件验证
- _session_persistence     — 旧持久化存根（已迁移至 _session_persistence_manager）
- _session_persistence_manager — 会话持久化管理器
- _session_messaging_manager   — 消息管理器
"""
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
    # _session_persistence_manager
    "SessionPersistenceManager",
    # _session_messaging_manager
    "SessionMessagingManager",
]
