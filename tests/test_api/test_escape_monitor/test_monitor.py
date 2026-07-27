"""EscapeMonitor 适配 Input 类后的单元测试。

验证 _dispatch_key_event 和 _handle_escape 正确委托到
InputParser / InputBuffer。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.tui.input._parser import KeyEvent
from src.api.escape_monitor._input_handler import StreamInputHandler


class TestDispatchKeyEvent:
    """_dispatch_key_event 分发逻辑测试。"""

    @pytest.fixture
    def monitor(self):
        """创建 mock EscapeMonitor 实例。"""
        # Input 在 EscapeMonitor.__init__ 内部通过相对导入创建
        with patch("src.tui.input._input.Input") as mock_input_cls:
            mock_input = MagicMock()
            mock_input.buffer = MagicMock()
            mock_input_cls.return_value = mock_input
            with patch("sys.stdin.fileno", return_value=0):
                from src.api.escape_monitor._monitor import EscapeMonitor
                monitor = EscapeMonitor()
        # Mock _input_handler
        monitor._input_handler = MagicMock(spec=StreamInputHandler)
        monitor._input = mock_input
        monitor._captured_input = bytearray()
        monitor._captured_lock = MagicMock()
        # Mock 回调方法
        monitor._do_interrupt = MagicMock()
        monitor._flush_stdin_residual = MagicMock()
        monitor._handle_tab = MagicMock()
        monitor._handle_special_key = MagicMock()
        monitor._dismiss_completion = MagicMock()
        monitor._trigger_auto_completion = MagicMock()
        monitor._handle_arrow_up = MagicMock()
        monitor._handle_arrow_down = MagicMock()
        return monitor

    def test_enter_dispatch(self, monitor):
        """Enter → 关闭补全 + 提交输入。"""
        event = KeyEvent(kind="enter")
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._enter.assert_called_once()

    def test_tab_dispatch(self, monitor):
        """Tab → 调用补全处理。"""
        event = KeyEvent(kind="tab")
        monitor._dispatch_key_event(event)
        monitor._handle_tab.assert_called_once()

    def test_backspace_dispatch(self, monitor):
        """Backspace → 关闭补全 + 退格 + 自动补全。"""
        event = KeyEvent(kind="backspace")
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._backspace.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_interrupt_dispatch(self, monitor):
        """Ctrl+C → 中断 + flush。"""
        event = KeyEvent(kind="interrupt")
        monitor._dispatch_key_event(event)
        monitor._do_interrupt.assert_called_once()
        monitor._flush_stdin_residual.assert_called_once()

    def test_home_dispatch(self, monitor):
        """Home → 关闭补全 + 移到行首。"""
        event = KeyEvent(kind="home")
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._home.assert_called_once()

    def test_end_dispatch(self, monitor):
        """End → 关闭补全 + 移到行尾。"""
        event = KeyEvent(kind="end")
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._end.assert_called_once()

    def test_delete_key_dispatch(self, monitor):
        """Delete 键 (mod=0) → 关闭补全 + 删除 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=0)
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._delete.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_ctrl_w_dispatch(self, monitor):
        """Ctrl+W (mod=1) → 关闭补全 + 删词 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=1)
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._delete_word_left.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_ctrl_u_dispatch(self, monitor):
        """Ctrl+U (mod=2) → 关闭补全 + 删到行首 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=2)
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._kill_to_bol.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_ctrl_k_dispatch(self, monitor):
        """Ctrl+K (mod=3) → 关闭补全 + 删到行尾 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=3)
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._kill_to_eol.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_ctrl_g_dispatch(self, monitor):
        """Ctrl+G → vim 编辑。"""
        event = KeyEvent(kind="ctrl_key", char='\x07')
        monitor._dispatch_key_event(event)
        monitor._handle_special_key.assert_called_once_with('vim')

    def test_ctrl_o_dispatch(self, monitor):
        """Ctrl+O → /editmsg。"""
        event = KeyEvent(kind="ctrl_key", char='\x0f')
        monitor._dispatch_key_event(event)
        monitor._handle_special_key.assert_called_once_with('editmsg')

    def test_ctrl_n_dispatch(self, monitor):
        """Ctrl+N → 切换模型。"""
        event = KeyEvent(kind="ctrl_key", char='\x0e')
        monitor._dispatch_key_event(event)
        monitor._handle_special_key.assert_called_once_with('switch_model')

    def test_ctrl_r_dispatch(self, monitor):
        """Ctrl+R → 切换模型。"""
        event = KeyEvent(kind="ctrl_key", char='\x12')
        monitor._dispatch_key_event(event)
        monitor._handle_special_key.assert_called_once_with('switch_model')

    def test_unknown_captures(self, monitor):
        """unknown → 关闭补全 + 捕获到 _captured_input。"""
        event = KeyEvent(kind="unknown", raw=b'\x00')
        monitor._dispatch_key_event(event)
        monitor._dismiss_completion.assert_called_once()
        assert monitor._captured_input == b'\x00'


class TestHandleEscape:
    """_handle_escape 委托 InputParser 测试。"""

    @pytest.fixture
    def monitor(self):
        """创建 mock EscapeMonitor 实例（含 mock Input.parse_sequence）。"""
        with patch("src.tui.input._input.Input") as mock_input_cls:
            mock_input = MagicMock()
            mock_input.buffer = MagicMock()
            mock_input.parse_sequence = MagicMock()
            mock_input_cls.return_value = mock_input
            with patch("sys.stdin.fileno", return_value=0):
                from src.api.escape_monitor._monitor import EscapeMonitor
                monitor = EscapeMonitor()
        monitor._input_handler = MagicMock(spec=StreamInputHandler)
        monitor._input = mock_input
        monitor._do_interrupt = MagicMock()
        monitor._flush_stdin_residual = MagicMock()
        monitor._dismiss_completion = MagicMock()
        monitor._trigger_auto_completion = MagicMock()
        monitor._handle_arrow_up = MagicMock()
        monitor._handle_arrow_down = MagicMock()
        return monitor

    def test_handle_escape_single_esc(self, monitor):
        """单 Esc → 中断 + flush。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(kind="escape")
            monitor._handle_escape()
        monitor._do_interrupt.assert_called_once()
        monitor._flush_stdin_residual.assert_called_once()

    def test_handle_escape_arrow_up(self, monitor):
        """上箭头 → _handle_arrow_up。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(kind="arrow_up")
            monitor._handle_escape()
        monitor._handle_arrow_up.assert_called_once()

    def test_handle_escape_arrow_right(self, monitor):
        """右箭头 → _right。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(kind="arrow_right")
            monitor._handle_escape()
        monitor._input_handler._right.assert_called_once()

    def test_handle_escape_ctrl_right(self, monitor):
        """Ctrl+右 → _word_right。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(
                kind="arrow_right", modifier=5,
            )
            monitor._handle_escape()
        monitor._input_handler._word_right.assert_called_once()

    def test_handle_escape_delete(self, monitor):
        """Delete 键 → 关闭补全 + 删除 + 自动补全。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(kind="delete")
            monitor._handle_escape()
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._delete.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_handle_escape_alt_backspace(self, monitor):
        """Alt+Backspace → 关闭补全 + 删词 + 自动补全。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(
                kind="backspace", modifier=1,
            )
            monitor._handle_escape()
        monitor._dismiss_completion.assert_called_once()
        monitor._input_handler._delete_word_left.assert_called_once()
        monitor._trigger_auto_completion.assert_called_once()

    def test_handle_escape_csi_u_shift_enter(self, monitor):
        """CSI u Shift+Enter → handle_char('\n')。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(
                kind="char", char="\n",
            )
            monitor._handle_escape()
        monitor._input_handler.handle_char.assert_called_once_with('\n')

    def test_handle_escape_interrupt(self, monitor):
        """双 Esc → 中断 + flush。"""
        with patch("sys.stdin.fileno", return_value=0):
            monitor._input.parse_sequence.return_value = KeyEvent(kind="interrupt")
            monitor._handle_escape()
        monitor._do_interrupt.assert_called_once()
        monitor._flush_stdin_residual.assert_called_once()
