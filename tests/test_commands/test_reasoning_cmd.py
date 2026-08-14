"""测试 /reasoning 命令 — 调整推理等级（low/medium/high/max）。

覆盖：设置等级、前缀匹配、模糊/未知提示、无参数显示当前、插件注册、
ConfigPort 为 None 时回退到 update_config、handle_command 分发。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.commands._config_cmd import _cmd_reasoning, _REASONING_LEVELS
from src.core.internal.commands._command_core import CommandContext, get_command_help


def _make_ctx(arg: str, config_port=None) -> CommandContext:
    return CommandContext(
        messages=[],
        state={"model": "deepseek-v4-pro"},
        arg=arg,
        build_system_prompt=lambda: [],
        get_user_input=lambda prompt="": "",
        context_manager=None,
        config_port=config_port,
    )


class TestReasoningCmdSet:
    """设置推理等级。"""

    def test_set_exact_level(self):
        port = MagicMock()
        port.get_reasoning_effort.return_value = "max"
        result = _cmd_reasoning(_make_ctx("high", port))
        assert result is True
        port.set.assert_called_once_with("reasoning_effort", "high")

    @pytest.mark.parametrize("arg,expected", [
        ("lo", "low"),
        ("me", "medium"),
        ("hi", "high"),
        ("ma", "max"),
        ("MAX", "max"),
        ("Low", "low"),
    ])
    def test_prefix_match(self, arg, expected):
        port = MagicMock()
        port.get_reasoning_effort.return_value = "max"
        result = _cmd_reasoning(_make_ctx(arg, port))
        assert result is True
        port.set.assert_called_once_with("reasoning_effort", expected)

    def test_ambiguous_prefix_reports_hint(self):
        """前缀匹配到多个等级（如 m → medium/max）时提示，不写入。"""
        port = MagicMock()
        port.get_reasoning_effort.return_value = "max"
        result = _cmd_reasoning(_make_ctx("m", port))
        assert result is True
        port.set.assert_not_called()

    def test_unknown_level_reports_hint(self):
        port = MagicMock()
        port.get_reasoning_effort.return_value = "max"
        result = _cmd_reasoning(_make_ctx("xyz", port))
        assert result is True
        port.set.assert_not_called()

    def test_fallback_to_update_config_when_no_port(self):
        """config_port 为 None 时回退到 update_config 写 RC。"""
        with patch("src.config.loader.RC_FILE") as mock_rc:
            mock_rc.exists.return_value = True
            mock_rc.read_text.return_value = "{}"
            result = _cmd_reasoning(_make_ctx("medium", None))
            assert result is True
            from src.config.loader import get_rc
            assert get_rc().get("reasoning_effort") == "medium"


class TestReasoningCmdShow:
    """无参数：显示当前推理等级。"""

    def test_show_current_no_write(self):
        port = MagicMock()
        port.get_reasoning_effort.return_value = "high"
        result = _cmd_reasoning(_make_ctx("", port))
        assert result is True
        port.set.assert_not_called()
        port.get_reasoning_effort.assert_called_once()

    def test_show_current_without_port(self):
        """config_port 为 None 时回退到 config 模块读取，不写 RC。"""
        with patch("src.config.proxy._config.REASONING_EFFORT", "low"):
            result = _cmd_reasoning(_make_ctx("", None))
            assert result is True

    def test_levels_constant(self):
        assert _REASONING_LEVELS == ["low", "medium", "high", "max"]


class TestReasoningCmdRegistry:
    """命令注册与帮助。"""

    def test_plugin_registered(self):
        from src.core.commands.base import get_plugin_registry
        plugin = get_plugin_registry().get("reasoning")
        assert plugin is not None
        assert plugin.meta.name == "reasoning"

    def test_command_help(self):
        assert get_command_help("/reasoning") == "调整推理等级 (low/medium/high/max)"

    def test_handle_command_dispatch(self):
        """通过 handle_command 分发执行。"""
        from src.core.commands import handle_command
        port = MagicMock()
        port.get_reasoning_effort.return_value = "max"
        handled = handle_command(
            "/reasoning low", [], {"model": "deepseek-v4-pro"},
            lambda: [], lambda p="": "",
            config_port=port,
        )
        assert handled is True
        port.set.assert_called_once_with("reasoning_effort", "low")
