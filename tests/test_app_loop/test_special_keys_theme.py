"""test_special_keys_theme — Ctrl+T 主题切换动作（Claude TUI parity 步骤 3.5）。

验证 _special_keys.py 工厂 'toggle_theme' 在 dark/light 间循环、返回文本不变、
set_theme 被调用且持久化。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.app_loop._special_keys import make_special_key_callback


class TestToggleTheme:
    def _factory(self, chat_ui=None):
        return make_special_key_callback(
            MagicMock(), MagicMock(), MagicMock(),
            chat_ui or MagicMock(),
        )

    def test_toggle_theme_returns_text_unchanged(self):
        """toggle_theme 不改变输入文本（返回原 text）。"""
        cb = self._factory()
        with patch("src.core.commands._ui_adapter.CommandUiAdapter") as Adapter:
            inst = Adapter.return_value
            inst.get_theme_names_with_desc.return_value = [
                ("dark", "暗色"), ("light", "亮色"), ("high-contrast", "高对比"),
            ]
            inst.get_active_theme.return_value = "dark"
            result = cb("toggle_theme", "hello")
        assert result == "hello"
        inst.set_theme.assert_called_once_with("light")

    def test_toggle_theme_cycles_dark_to_light_to_dark(self):
        """dark → light → dark 循环切换。"""
        cb = self._factory()
        with patch("src.core.commands._ui_adapter.CommandUiAdapter") as Adapter:
            inst = Adapter.return_value
            inst.get_theme_names_with_desc.return_value = [
                ("dark", "暗色"), ("light", "亮色"),
            ]
            inst.get_active_theme.return_value = "dark"
            cb("toggle_theme", "")
            inst.get_active_theme.return_value = "light"
            cb("toggle_theme", "")
        assert inst.set_theme.call_args_list[0][0][0] == "light"
        assert inst.set_theme.call_args_list[1][0][0] == "dark"

    def test_toggle_theme_single_theme_noop(self):
        """仅一个主题时不切换（no-op，不抛异常）。"""
        cb = self._factory()
        with patch("src.core.commands._ui_adapter.CommandUiAdapter") as Adapter:
            inst = Adapter.return_value
            inst.get_theme_names_with_desc.return_value = [("dark", "暗色")]
            result = cb("toggle_theme", "x")
        assert result == "x"
        inst.set_theme.assert_not_called()
