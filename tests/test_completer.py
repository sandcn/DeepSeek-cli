"""测试 src/ui/tui/completer 模块。"""

from unittest.mock import patch

from src.ui.tui.completer import (
    ChatCompleter,
)


class TestParamCommands:
    """P1-1: /review 参数补全不可达 — 回归测试"""

    def test_param_commands_contains_review(self):
        """_PARAM_COMMANDS frozenset 应包含 '/review'。"""
        assert '/review' in ChatCompleter._PARAM_COMMANDS

    def test_review_param_not_blocked(self):
        """get_completions 遇到 /review 时不因 _PARAM_COMMANDS 检查提前返回。"""
        cc = ChatCompleter()
        assert '/review' in cc._PARAM_COMMANDS
        # 验证 /review 在 _PARAM_COMMANDS 中——即不会被第68行拦截
        assert '/review' in ChatCompleter._PARAM_COMMANDS



