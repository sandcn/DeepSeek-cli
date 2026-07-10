"""命令内部实现 — 命令注册表和分发"""
from __future__ import annotations

from ._command_core import *
from ._command_core import _commands  # noqa: F401 — _ 前缀，显式导入

__all__ = [
    # _command_core
    "register_command", "handle_command",
    "CommandContext", "get_registered_command_names",
    "COMMANDS_HELP", "_commands",
    "_format_cost_duration", "show_cost",
    "_pop_assistant_tool_messages", "_cmd_help",
    "get_dynamic_help_text",
]
