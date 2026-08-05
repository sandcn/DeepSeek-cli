"""命令系统 — 保持向后兼容的导出

现有 _commands_old.py 已删除，所有符号直接由子模块提供。
"""

# 为了保持向后兼容（mock.patch 等测试工具依赖这些名称）,
# 从各子模块直接导入
from ..internal.commands._command_core import (
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

from ._session_cmd import (
    _cmd_clear,
    _cmd_pin,
    _cmd_undo,
    _cmd_retry,
    _cmd_edit,
    _cmd_editmsg,
    _cmd_changes,
    # 插件子类
    ClearCommand,
    PinCommand,
    UndoCommand,
    RetryCommand,
    EditCommand,
    ChangesCommand,
)

from ._config_cmd import (
    _cmd_model,
    _cmd_system,
    _cmd_cost,
    _cmd_theme,
    # 插件子类
    SystemCommand,
    CostCommand,
    ThemeCommand,
)

from ._data_cmd import (
    _cmd_load,
    _cmd_sessions,
    # 插件子类
    LoadCommand,
    SessionsCommand,
    HelpCommand,
)

from ._export_cmd import (
    _cmd_export,
    build_markdown,
    # 插件子类
    ExportCommand,
)

from ._ui_adapter import CommandUiAdapter

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
    # UI 适配器
    "CommandUiAdapter",
    # 插件化新增
    "CommandPlugin",
    "CommandMeta",
    "CommandPluginRegistry",
    "command_plugin",
    "get_plugin_registry",
]
