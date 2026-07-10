"""命令系统入口 — 从各子模块直接导出所有符号

保持与现有导入（app_loop.py、ui/input.py、webui/ws_handler/commands.py）
的向后兼容。所有命令符号由各子模块直接提供。
"""

# 各子模块的 import 副作用会执行 register_command() 调用
from .internal._command_core import (  # noqa: F401, F811
    register_command,
    handle_command,
    CommandContext,
    get_registered_command_names,
    COMMANDS_HELP,
    _commands,
    _format_cost_duration,
    show_cost,
    _pop_assistant_tool_messages,
    _cmd_help,
)

from .commands._session_cmd import (  # noqa: F401, F811
    _cmd_clear,
    _cmd_compress,
    _cmd_pin,
    _cmd_undo,
    _cmd_retry,
    _cmd_edit,
    _cmd_editmsg,
    _cmd_changes,
)

from .commands._config_cmd import (  # noqa: F401, F811
    _cmd_model,
    _cmd_system,
    _cmd_cost,
    _cmd_theme,
)

from .commands._data_cmd import (  # noqa: F401, F811
    _cmd_init,
    _cmd_load,
    _cmd_sessions,
)

__all__ = [
    "register_command",
    "handle_command",
    "CommandContext",
    "get_registered_command_names",
    "COMMANDS_HELP",
    "_commands",
    "_pop_assistant_tool_messages",
    "_format_cost_duration",
    "_cmd_clear",
    "_cmd_compress",
    "_cmd_pin",
    "_cmd_undo",
    "_cmd_retry",
    "_cmd_edit",
    "_cmd_model",
    "_cmd_system",
    "_cmd_cost",
    "_cmd_init",
    "_cmd_help",
    "_cmd_load",
    "_cmd_sessions",
    "_cmd_theme",
    "_cmd_editmsg",
    "_cmd_changes",
    "show_cost",
]
