"""EscapeMonitor 统一输入重构后的单元测试。

验证：
  - Input._dispatch_key_event 正确分发到 InputBuffer
  - EscapeMonitor._handle_escape 推送事件到 Input 队列（非中断）或内联处理中断
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.tui.input._parser import KeyEvent
from src.tui.input._input import Input, InputEvent


# ── TestInputDispatchKeyEvent ──────────────────────────────────────

class TestInputDispatchKeyEvent:
    """Input._dispatch_key_event 分发逻辑测试。"""

    @pytest.fixture
    def input_(self, tmp_path):
        """创建 Input 实例，mock 内部 buffer 和回调。"""
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24
        inp = Input(
            fd=0,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_cache,
        )
        inp._buffer = MagicMock()
        inp._handle_tab = MagicMock()
        inp._dismiss_completion = MagicMock()
        inp._trigger_auto_completion = MagicMock()
        inp._handle_arrow_up = MagicMock()
        inp._handle_arrow_down = MagicMock()
        inp._handle_special_key_action = MagicMock()
        inp._special_key_callback = None
        inp._completion_callback = None
        inp._completion_navigate_callback = None
        inp._dismiss_completion_callback = None
        inp._auto_completion_callback = None
        return inp

    def test_enter_dispatch(self, input_):
        """Enter → 关闭补全 + 提交输入。"""
        event = KeyEvent(kind="enter")
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._enter.assert_called_once()

    def test_tab_dispatch(self, input_):
        """Tab → 调用补全处理。"""
        event = KeyEvent(kind="tab")
        input_._dispatch_key_event(event)
        input_._handle_tab.assert_called_once()

    def test_backspace_dispatch(self, input_):
        """Backspace → 关闭补全 + 退格 + 自动补全。"""
        event = KeyEvent(kind="backspace")
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._backspace.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_home_dispatch(self, input_):
        """Home → 关闭补全 + 移到行首。"""
        event = KeyEvent(kind="home")
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._home.assert_called_once()

    def test_end_dispatch(self, input_):
        """End → 关闭补全 + 移到行尾。"""
        event = KeyEvent(kind="end")
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._end.assert_called_once()

    def test_delete_key_dispatch(self, input_):
        """Delete 键 (mod=0) → 关闭补全 + 删除 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=0)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._delete.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_ctrl_w_dispatch(self, input_):
        """Ctrl+W (mod=1) → 关闭补全 + 删词 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=1)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._delete_word_left.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_ctrl_u_dispatch(self, input_):
        """Ctrl+U (mod=2) → 关闭补全 + 删到行首 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=2)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._kill_to_bol.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_ctrl_k_dispatch(self, input_):
        """Ctrl+K (mod=3) → 关闭补全 + 删到行尾 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=3)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._kill_to_eol.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_arrow_up_dispatch(self, input_):
        """上箭头 → _handle_arrow_up。"""
        event = KeyEvent(kind="arrow_up")
        input_._dispatch_key_event(event)
        input_._handle_arrow_up.assert_called_once()

    def test_arrow_right_dispatch(self, input_):
        """右箭头 → _right。"""
        event = KeyEvent(kind="arrow_right")
        input_._dispatch_key_event(event)
        input_._buffer._right.assert_called_once()

    def test_ctrl_right_dispatch(self, input_):
        """Ctrl+右 → _word_right。"""
        event = KeyEvent(kind="arrow_right", modifier=5)
        input_._dispatch_key_event(event)
        input_._buffer._word_right.assert_called_once()

    def test_arrow_left_dispatch(self, input_):
        """左箭头 → _left。"""
        event = KeyEvent(kind="arrow_left")
        input_._dispatch_key_event(event)
        input_._buffer._left.assert_called_once()

    def test_ctrl_left_dispatch(self, input_):
        """Ctrl+左 → _word_left。"""
        event = KeyEvent(kind="arrow_left", modifier=5)
        input_._dispatch_key_event(event)
        input_._buffer._word_left.assert_called_once()

    def test_char_newline_dispatch(self, input_):
        """CSI u Shift+Enter (kind=char, char='\n') → handle_char('\n')。"""
        event = KeyEvent(kind="char", char="\n")
        input_._dispatch_key_event(event)
        input_._buffer.handle_char.assert_called_once_with('\n')

    def test_unknown_captures(self, input_):
        """unknown → 关闭补全 + 捕获到 _captured_input。"""
        event = KeyEvent(kind="unknown", raw=b'\x00')
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        # Verify capture via the lock
        assert bytes(input_._captured_input) == b'\x00'

    def test_alt_backspace_dispatch(self, input_):
        """Alt+Backspace (backspace, mod=1) → 关闭补全 + 删词 + 自动补全。"""
        event = KeyEvent(kind="backspace", modifier=1)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._buffer._delete_word_left.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()


# ── TestHandleEscape ───────────────────────────────────────────

class TestHandleEscape:
    """EscapeMonitor._handle_escape 推送事件测试。"""

    @pytest.fixture
    def monitor_and_input(self):
        """创建 EscapeMonitor（注入 mock Input），返回 (monitor, mock_input)。"""
        mock_input = MagicMock(spec=Input)
        mock_input.push_key_event = MagicMock()
        mock_input.parse_sequence = MagicMock()
        mock_input.has_queued_input = MagicMock(return_value=False)
        mock_input.reset_and_echo = MagicMock()

        with patch("sys.stdin.fileno", return_value=0):
            from src.api.escape_monitor._monitor import EscapeMonitor
            monitor = EscapeMonitor(input_instance=mock_input)
        monitor._do_interrupt = MagicMock()
        monitor._flush_stdin_residual = MagicMock()
        return monitor, mock_input

    def test_handle_escape_single_esc(self, monitor_and_input):
        """单 Esc → 中断 + flush（内联，不入队）。"""
        monitor, mock_input = monitor_and_input
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = KeyEvent(kind="escape")
            monitor._handle_escape()
        monitor._do_interrupt.assert_called_once()
        monitor._flush_stdin_residual.assert_called_once()
        mock_input.push_key_event.assert_not_called()

    def test_handle_escape_interrupt(self, monitor_and_input):
        """双 Esc (interrupt) → 中断 + flush（内联）。"""
        monitor, mock_input = monitor_and_input
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = KeyEvent(kind="interrupt")
            monitor._handle_escape()
        monitor._do_interrupt.assert_called_once()
        monitor._flush_stdin_residual.assert_called_once()
        mock_input.push_key_event.assert_not_called()

    def test_handle_escape_arrow_up(self, monitor_and_input):
        """上箭头 → 推送 KeyEvent 到 Input 队列。"""
        monitor, mock_input = monitor_and_input
        event = KeyEvent(kind="arrow_up")
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = event
            monitor._handle_escape()
        mock_input.push_key_event.assert_called_once_with(event)

    def test_handle_escape_arrow_right(self, monitor_and_input):
        """右箭头 → 推送 KeyEvent 到 Input 队列。"""
        monitor, mock_input = monitor_and_input
        event = KeyEvent(kind="arrow_right")
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = event
            monitor._handle_escape()
        mock_input.push_key_event.assert_called_once_with(event)

    def test_handle_escape_ctrl_right(self, monitor_and_input):
        """Ctrl+右 → 推送 KeyEvent（含 modifier=5）到 Input 队列。"""
        monitor, mock_input = monitor_and_input
        event = KeyEvent(kind="arrow_right", modifier=5)
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = event
            monitor._handle_escape()
        mock_input.push_key_event.assert_called_once_with(event)

    def test_handle_escape_delete(self, monitor_and_input):
        """Delete 键 → 推送 KeyEvent 到 Input 队列。"""
        monitor, mock_input = monitor_and_input
        event = KeyEvent(kind="delete")
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = event
            monitor._handle_escape()
        mock_input.push_key_event.assert_called_once_with(event)

    def test_handle_escape_home(self, monitor_and_input):
        """Home → 推送 KeyEvent 到 Input 队列。"""
        monitor, mock_input = monitor_and_input
        event = KeyEvent(kind="home")
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = event
            monitor._handle_escape()
        mock_input.push_key_event.assert_called_once_with(event)

    def test_handle_escape_csi_u_shift_enter(self, monitor_and_input):
        """CSI u Shift+Enter (kind=char, char='\n') → 推送 KeyEvent 到 Input 队列。"""
        monitor, mock_input = monitor_and_input
        event = KeyEvent(kind="char", char="\n")
        with patch("sys.stdin.fileno", return_value=0):
            mock_input.parse_sequence.return_value = event
            monitor._handle_escape()
        mock_input.push_key_event.assert_called_once_with(event)
