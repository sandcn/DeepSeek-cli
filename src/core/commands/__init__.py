"""命令系统 — 保持向后兼容的导出

现有 _commands_old.py 已删除，所有符号直接由子模块提供。
"""

# 为了保持向后兼容（mock.patch 等测试工具依赖这些名称）,
# 从各子模块直接导入
from .._command_core import (
    register_command,
    handle_command,
    CommandContext,
    get_registered_command_names,
    COMMANDS_HELP,
    # 以下为测试需要引用的内部符号
    _commands,
    _pop_assistant_tool_messages,
    _format_cost_duration,
)

from ..commands_session import (
    _cmd_clear,
    _cmd_compress,
    _cmd_pin,
    _cmd_undo,
    _cmd_retry,
    _cmd_edit,
    _cmd_editmsg,
    _cmd_changes,
)

from ..commands_config import (
    _cmd_model,
    _cmd_system,
    _cmd_cost,
    _cmd_theme,
)

from ..commands_data import (
    _cmd_init,
    _cmd_load,
    _cmd_sessions,
)

from .base import (
    CommandPlugin,
    CommandMeta,
    CommandPluginRegistry,
    command_plugin,
    get_plugin_registry,
)

__all__ = [
    # 原有兼容
    "register_command",
    "handle_command",
    "CommandContext",
    "get_registered_command_names",
    "COMMANDS_HELP",
    # 插件化新增
    "CommandPlugin",
    "CommandMeta",
    "CommandPluginRegistry",
    "command_plugin",
    "get_plugin_registry",
]
