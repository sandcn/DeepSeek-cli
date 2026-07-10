"""core/internal — 内部实现模块（按领域分组）

子包结构：
- session/ — 会话消息管理、状态、持久化、压缩
- agent/ — 工具回调、子代理生成、输出捕获
- commands/ — 命令注册表
- shared/ — 缓存、沙盒历史

【架构】此目录为纯实现细节。外部代码统一通过 src.core.internal 子包入口导入。
"""
from __future__ import annotations

from .session import (
    add_message, non_system_messages, system_messages,
    CoreHooks, SessionState,
    _validate_compress_preconditions,
    save_session, load_session_data,
    save_checkpoint_session, clear_checkpoint_session,
    has_checkpoint_session, resume_from_checkpoint_session,
    load_checkpoint_data, safe_save_state,
    get_session_ids_fn, list_sessions_fn,
    SessionPersistenceManager, SessionMessagingManager,
)
from .agent import (
    ToolCallbackChain, SubAgentSpawner, CaptureManager,
)
from .commands._command_core import (  # noqa: E402 — 直接导入子模块
    register_command, handle_command,
    CommandContext, get_registered_command_names,
    COMMANDS_HELP, _commands,
    _format_cost_duration, show_cost,
    _pop_assistant_tool_messages, _cmd_help,
    get_dynamic_help_text,
)
from .shared import (
    MessageStatsCache, FileSnapshot,
)

__all__ = [
    # session 子包
    "add_message", "non_system_messages", "system_messages",
    "CoreHooks", "SessionState",
    "_validate_compress_preconditions",
    "save_session", "load_session_data",
    "save_checkpoint_session", "clear_checkpoint_session",
    "has_checkpoint_session", "resume_from_checkpoint_session",
    "load_checkpoint_data", "safe_save_state",
    "get_session_ids_fn", "list_sessions_fn",
    "SessionPersistenceManager", "SessionMessagingManager",
    # agent 子包
    "ToolCallbackChain", "SubAgentSpawner", "CaptureManager",
    # commands 子包
    "register_command", "handle_command",
    "CommandContext", "get_registered_command_names",
    "COMMANDS_HELP", "_commands",
    "_format_cost_duration", "show_cost",
    "_pop_assistant_tool_messages", "_cmd_help",
    "get_dynamic_help_text",
    # shared 子包
    "MessageStatsCache", "FileSnapshot",
]
