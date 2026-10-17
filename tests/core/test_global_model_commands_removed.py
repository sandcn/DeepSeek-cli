"""全局 /temperature 与 /reasoning 命令删除的回归测试。

2026-10-17 用户需求：「/models 支持设置对应的温度跟思考级别，删除以前
全局的命令」——温度/思考级别改为在 /models 中按模型档案设置；旧的全局
命令必须完全下线（清单条目不注册、帮助文本不列出、插件类不存在）。
"""

from __future__ import annotations

import importlib

import pytest


def _registry():
    importlib.import_module("src.core.commands.plugins.models_plugin")
    from src.core.commands.base import get_plugin_registry
    return get_plugin_registry()


@pytest.mark.parametrize("name", ["temperature", "reasoning"])
def test_global_commands_not_registered(name):
    assert not _registry().exists(name)


def test_manifest_entries_removed():
    from src.plugins.manifest_entries import COMMAND_PLUGIN_ENTRIES
    ids = {entry.get("id") for entry in COMMAND_PLUGIN_ENTRIES}
    assert "cmd_temperature" not in ids
    assert "cmd_reasoning" not in ids
    assert "cmd_models" in ids


def test_help_text_replaced_with_models():
    from src.core.internal.commands._command_core import COMMANDS_HELP
    assert "/temperature" not in COMMANDS_HELP
    assert "/reasoning" not in COMMANDS_HELP
    assert "/models" in COMMANDS_HELP


def test_plugin_classes_removed():
    from src.core.commands import _config_cmd
    assert not hasattr(_config_cmd, "TemperatureCommand")
    assert not hasattr(_config_cmd, "ReasoningCommand")
    assert not hasattr(_config_cmd, "_cmd_temperature")
    assert not hasattr(_config_cmd, "_cmd_reasoning")


def test_models_still_available():
    assert _registry().exists("models")
    assert _registry().exists("model")
