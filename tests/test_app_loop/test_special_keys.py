"""test_special_keys — 特殊按键回调单元测试。

验证 _special_keys.py 中 vim 路径使用 EscapeMonitor 公开 API
和 Input.flush_stdin_buffer()。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════
# TestVimPathUsesPublicAPI
# ═══════════════════════════════════════════════════════════

class TestVimPathUsesPublicAPI:
    """验证 vim 路径使用 EscapeMonitor 公开方法。"""

    @pytest.fixture
    def mock_monitor(self):
        """创建 mock EscapeMonitor 实例。"""
        m = MagicMock()
        m.restore_terminal_settings = MagicMock()
        m.apply_monitor_settings = MagicMock()
        return m

    @pytest.fixture
    def mock_input(self):
        """创建 mock Input 实例。"""
        m = MagicMock()
        m.flush_stdin_buffer = MagicMock()
        return m

    @pytest.fixture
    def mock_chat_ui(self, mock_input):
        """创建 mock chat_ui 实例（含 input 属性）。"""
        m = MagicMock()
        m._components = MagicMock()
        m._components.input = mock_input
        m.input = mock_input
        m.teardown_bottom_bar = MagicMock()
        m.setup_bottom_bar = MagicMock()
        return m

    def _get_callback(self, chat_ui=None, monitor=None):
        """辅助函数：创建回调并返回。"""
        from src.app_loop._special_keys import make_special_key_callback
        loop = MagicMock()
        session = MagicMock()
        state = MagicMock()
        return make_special_key_callback(loop, session, state, chat_ui, monitor)

    def test_vim_calls_restore_terminal_settings(self, mock_monitor, mock_chat_ui):
        """vim 动作应调用 monitor.restore_terminal_settings()（公开方法）。"""
        callback = self._get_callback(chat_ui=mock_chat_ui, monitor=mock_monitor)

        # 让 edit_in_vim_sync 返回空（模拟 vim 编辑）
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.app_loop._special_keys.edit_in_vim_sync",
                lambda t: t,
            )
            callback('vim', 'test text')

        mock_monitor.restore_terminal_settings.assert_called_once()
        mock_monitor.apply_monitor_settings.assert_called_once()

    def test_vim_calls_flush_stdin_buffer(self, mock_monitor, mock_chat_ui, mock_input):
        """vim 退出后应调用 input_.flush_stdin_buffer()。"""
        callback = self._get_callback(chat_ui=mock_chat_ui, monitor=mock_monitor)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.app_loop._special_keys.edit_in_vim_sync",
                lambda t: t,
            )
            callback('vim', 'test text')

        mock_input.flush_stdin_buffer.assert_called_once()

    def test_vim_does_not_call_private_methods(self, mock_monitor, mock_chat_ui):
        """vim 路径不应调用 EscapeMonitor 私有方法 _restore_terminal_settings / _apply_monitor_settings。"""
        # 给 monitor 添加私有方法 mock（如果存在）
        mock_monitor._restore_terminal_settings = MagicMock()
        mock_monitor._apply_monitor_settings = MagicMock()

        callback = self._get_callback(chat_ui=mock_chat_ui, monitor=mock_monitor)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.app_loop._special_keys.edit_in_vim_sync",
                lambda t: t,
            )
            callback('vim', 'test text')

        # 私有方法不应被直接调用（它们通过 deprecated alias 可能被间接触发，
        # 但 _special_keys.py 不应直接调用它们）
        mock_monitor._restore_terminal_settings.assert_not_called()
        mock_monitor._apply_monitor_settings.assert_not_called()

    def test_vim_monitor_none_safe(self):
        """monitor=None 时 vim 路径不应崩溃。"""
        callback = self._get_callback(chat_ui=None, monitor=None)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "src.app_loop._special_keys.edit_in_vim_sync",
                lambda t: t,
            )
            result = callback('vim', 'test text')

        assert result == 'test text'

    def test_editmsg_action(self):
        """editmsg 动作应返回 '/editmsg'。"""
        callback = self._get_callback()
        result = callback('editmsg', 'some text')
        assert result == '/editmsg'

    def test_unknown_action(self):
        """未知动作应返回 None。"""
        callback = self._get_callback()
        result = callback('unknown_action', 'text')
        assert result is None
