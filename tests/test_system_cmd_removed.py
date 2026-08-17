"""/system 命令已删除测试。

覆盖链路：
1. 符号级删除（_config_cmd 不再定义 _cmd_system / SystemCommand）
2. 导出级删除（src.core.commands 不再导出相关符号）
3. 插件注册表（get_plugin_registry 不含 system）
4. 旧命令注册表（get_registered_command_names 不含 /system）
5. handle_command 集成（/system 不被处理）
6. 帮助文本（COMMANDS_HELP / get_dynamic_help_text 不再含 /system）
7. 回归：其余配置命令仍正常注册
"""
from __future__ import annotations

import pytest

import src.core.commands as cmds
from src.core.commands._config_cmd import _cmd_temperature  # noqa: F401
from src.core.commands import get_plugin_registry
from src.core.internal.commands._command_core import (
    CommandContext,
    handle_command,
    get_registered_command_names,
    COMMANDS_HELP,
    get_dynamic_help_text,
)


def _make_ctx(arg: str, messages=None):
    return CommandContext(
        messages=messages if messages is not None else [],
        state={}, arg=arg,
        build_system_prompt=None, get_user_input=None, context_manager=None,
        session=None, persistence_port=None, config_port=None, ui_adapter=None,
    )


class TestSymbolRemoved:
    def test_config_cmd_module_has_no_cmd_system(self):
        assert not hasattr(cmds._config_cmd, "_cmd_system")
        assert not hasattr(cmds._config_cmd, "SystemCommand")

    def test_commands_package_does_not_export_cmd_system(self):
        assert not hasattr(cmds, "_cmd_system")
        assert not hasattr(cmds, "SystemCommand")


class TestRegistry:
    def test_plugin_registry_has_no_system(self):
        registry = get_plugin_registry()
        assert registry.exists("system") is False
        assert registry.get("system") is None

    def test_old_registry_has_no_system(self):
        names = get_registered_command_names()
        assert "/system" not in names
        assert "system" not in names

    def test_handle_command_system_not_handled(self):
        assert handle_command("/system", [], {}, None, None) is False
        assert handle_command("/system 追加内容", [], {}, None, None) is False


class TestHelpText:
    def test_commands_help_has_no_system(self):
        assert "/system" not in COMMANDS_HELP

    def test_dynamic_help_has_no_system(self):
        assert "/system" not in get_dynamic_help_text()


class TestRegression:
    def test_other_config_commands_still_registered(self):
        import src.core.commands.plugins  # noqa: F401  # 触发 ModelPlugin 等插件注册
        registry = get_plugin_registry()
        for name in ("model", "cost", "theme", "reasoning", "temperature"):
            assert registry.exists(name) is True, f"命令 /{name} 不应被误删"

    def test_other_config_commands_still_exported(self):
        for symbol in ("_cmd_model", "_cmd_cost", "_cmd_theme", "_cmd_reasoning", "_cmd_temperature"):
            assert hasattr(cmds._config_cmd, symbol), f"{symbol} 不应被误删"

    def test_temperature_cmd_still_works(self):
        assert _cmd_temperature(_make_ctx("0.5")) is True
