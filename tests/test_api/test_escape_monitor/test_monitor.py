"""EscapeMonitor 统一输入重构后的单元测试。

验证：
  - EscapeMonitor 生命周期：start/stop/resume 委托 Input 的 I/O 线程方法
  - EscapeMonitor._handle_special_key 终端模式切换 + 回调委托
  - EscapeMonitor 属性委托：interrupted / is_alive
  - Input._dispatch_key_event 正确分发到缓冲操作
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from src.tui.input import KeyEvent, Input, InputEvent


# ── TestEscapeMonitorLifecycle ──────────────────────────────────

class TestEscapeMonitorLifecycle:
    """EscapeMonitor start/stop/resume 生命周期测试。"""

    @pytest.fixture
    def mock_input(self):
        """创建 mock Input 实例。"""
        mock = MagicMock(spec=Input)
        mock.start_io = MagicMock()
        mock.stop_io = MagicMock()
        mock.resume_io = MagicMock()
        mock.pause_io = MagicMock()
        mock.reset = MagicMock()
        mock.load_history = MagicMock()
        mock.set_buffer = MagicMock()
        mock.echo = MagicMock()
        mock.get_current_text = MagicMock(return_value="")
        mock.is_io_running = False
        mock.interrupted = False
        mock.has_queued_input = MagicMock(return_value=False)
        mock.reset_and_echo = MagicMock()
        mock._flush_stdin_residual = MagicMock()
        mock._interrupted = MagicMock()
        return mock

    @pytest.fixture
    def monitor(self, mock_input):
        """创建 EscapeMonitor 实例（注入 mock Input）。"""
        from src.api.escape_monitor._monitor import EscapeMonitor
        m = EscapeMonitor(input_instance=mock_input)
        m._apply_monitor_settings = MagicMock()
        m._restore_terminal_settings = MagicMock()
        m._restore_terminal_settings_impl = MagicMock()
        return m

    def test_init_requires_input_instance(self):
        """__init__ 无 input_instance 时应抛出 ValueError。"""
        from src.api.escape_monitor._monitor import EscapeMonitor
        with pytest.raises(ValueError, match="需要有效的 Input 实例"):
            EscapeMonitor()

    def test_start_delegates_to_input(self, monitor, mock_input):
        """start() 应调用 input.start_io()。"""
        monitor.start()
        mock_input.reset.assert_called_once()
        mock_input.load_history.assert_called_once()
        mock_input.start_io.assert_called_once()
        monitor._apply_monitor_settings.assert_called_once()

    def test_start_with_prefill(self, monitor, mock_input):
        """start(prefill=...) 应调用 input.set_buffer(prefill)。"""
        monitor.start(prefill="hello")
        mock_input.set_buffer.assert_called_once_with("hello")

    def test_stop_delegates_to_input(self, monitor, mock_input):
        """stop() 应调用 input.stop_io()。"""
        monitor.start()
        monitor.stop()
        mock_input.stop_io.assert_called_once()
        monitor._restore_terminal_settings.assert_called()

    def test_resume_delegates_to_input(self, monitor, mock_input):
        """resume() 应调用 input.resume_io()。"""
        monitor.start()
        monitor.resume()
        mock_input.resume_io.assert_called_once()

    def test_interrupted_delegates_to_input(self, monitor, mock_input):
        """interrupted 属性应委托给 input.interrupted。"""
        mock_input.interrupted = True
        assert monitor.interrupted is True
        mock_input.interrupted = False
        assert monitor.interrupted is False

    def test_is_alive_delegates_to_input(self, monitor, mock_input):
        """is_alive 属性应委托给 input.is_io_running。"""
        mock_input.is_io_running = True
        assert monitor.is_alive is True
        mock_input.is_io_running = False
        assert monitor.is_alive is False


# ── TestHandleSpecialKey ───────────────────────────────────

class TestHandleSpecialKey:
    """EscapeMonitor._handle_special_key 终端模式切换测试。"""

    @pytest.fixture
    def mock_input(self):
        """创建 mock Input 实例。"""
        mock = MagicMock(spec=Input)
        mock.get_current_text = MagicMock(return_value="test input")
        mock._special_key_callback = None
        mock.pause_io = MagicMock()
        mock.resume_io = MagicMock()
        return mock

    @pytest.fixture
    def monitor(self, mock_input):
        """创建 EscapeMonitor（注入 mock Input）。"""
        from src.api.escape_monitor._monitor import EscapeMonitor
        m = EscapeMonitor(input_instance=mock_input)
        m._restore_terminal_settings = MagicMock()
        m._apply_monitor_settings = MagicMock()
        return m

    def test_handle_special_key_no_callback(self, monitor, mock_input):
        """无回调时应返回原文本。"""
        result = monitor._handle_special_key('vim', 'some text')
        assert result == 'some text'

    def test_handle_special_key_self_reference_guard(self, monitor, mock_input):
        """当 Input._special_key_callback 指向自身时应返回原文本（防递归）。"""
        mock_input._special_key_callback = monitor._handle_special_key
        result = monitor._handle_special_key('vim', 'some text')
        assert result == 'some text'

    def test_handle_special_key_calls_real_callback(self, monitor, mock_input):
        """应暂停终端、调用真实回调、恢复终端。"""
        real_cb = MagicMock(return_value="modified text")
        mock_input._special_key_callback = real_cb

        result = monitor._handle_special_key('vim', 'original')

        monitor._restore_terminal_settings.assert_called_once()
        mock_input.pause_io.assert_called_once()
        real_cb.assert_called_once_with('vim', 'original')
        monitor._apply_monitor_settings.assert_called_once()
        mock_input.resume_io.assert_called_once()
        assert result == "modified text"

    def test_handle_special_key_callback_exception(self, monitor, mock_input):
        """回调异常时应恢复终端并返回原文本。"""
        real_cb = MagicMock(side_effect=RuntimeError("callback failed"))
        mock_input._special_key_callback = real_cb

        result = monitor._handle_special_key('editmsg', 'safe text')

        monitor._restore_terminal_settings.assert_called_once()
        mock_input.pause_io.assert_called_once()
        monitor._apply_monitor_settings.assert_called_once()
        mock_input.resume_io.assert_called_once()
        assert result == 'safe text'


# ── TestDoInterrupt ────────────────────────────────────────

class TestDoInterrupt:
    """EscapeMonitor._do_interrupt 外部中断测试。"""

    @pytest.fixture
    def mock_input(self):
        """创建 mock Input 实例。"""
        mock = MagicMock(spec=Input)
        mock.has_queued_input = MagicMock(return_value=False)
        mock.reset_and_echo = MagicMock()
        mock._flush_stdin_residual = MagicMock()
        mock._interrupted = MagicMock()
        return mock

    @pytest.fixture
    def monitor(self, mock_input):
        """创建 EscapeMonitor（注入 mock Input）。"""
        from src.api.escape_monitor._monitor import EscapeMonitor
        return EscapeMonitor(input_instance=mock_input)

    def test_do_interrupt_no_queued_input(self, monitor, mock_input):
        """无排队输入时调用 reset_and_echo。"""
        mock_input.has_queued_input.return_value = False
        with patch("src.api.escape_monitor._monitor.request_interrupt_async") as mock_req:
            monitor._do_interrupt()
        mock_input.reset_and_echo.assert_called_once()
        mock_input._interrupted.set.assert_called_once()
        mock_req.assert_called_once()

    def test_do_interrupt_with_queued_input(self, monitor, mock_input):
        """有排队输入时调用 _flush_stdin_residual。"""
        mock_input.has_queued_input.return_value = True
        with patch("src.api.escape_monitor._monitor.request_interrupt_async") as mock_req:
            monitor._do_interrupt()
        mock_input._flush_stdin_residual.assert_called_once()
        mock_input._interrupted.set.assert_called_once()
        mock_req.assert_called_once()

    def test_do_interrupt_stopped(self, monitor, mock_input):
        """已停止时 _do_interrupt 应直接返回。"""
        monitor._stop.set()
        mock_input.has_queued_input.return_value = False
        with patch("src.api.escape_monitor._monitor.request_interrupt_async") as mock_req:
            monitor._do_interrupt()
        mock_input.reset_and_echo.assert_not_called()
        mock_req.assert_not_called()


# ── TestInputDispatchKeyEvent ──────────────────────────────────────

class TestInputDispatchKeyEvent:
    """Input._dispatch_key_event 分发逻辑测试。"""

    @pytest.fixture
    def input_(self, tmp_path):
        """创建 Input 实例，mock 内部方法和回调。

        统一 Input 类中 _dispatch_key_event 直接调用 self._backspace() /
        self._enter() / self._delete() 等方法（不再通过 _buffer 子对象），
        因此直接 mock Input 实例上的这些方法。
        """
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24
        inp = Input(
            fd=0,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_cache,
        )
        # Mock 内部编辑方法（统一 Input 类直接调用 self._xxx，不通过 _buffer）
        inp._enter = MagicMock()
        inp._backspace = MagicMock()
        inp._delete = MagicMock()
        inp._delete_word_left = MagicMock()
        inp._kill_to_bol = MagicMock()
        inp._kill_to_eol = MagicMock()
        inp._home = MagicMock()
        inp._end = MagicMock()
        inp._left = MagicMock()
        inp._right = MagicMock()
        inp._word_left = MagicMock()
        inp._word_right = MagicMock()
        inp.handle_char = MagicMock()
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
        input_._enter.assert_called_once()

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
        input_._backspace.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_home_dispatch(self, input_):
        """Home → 关闭补全 + 移到行首。"""
        event = KeyEvent(kind="home")
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._home.assert_called_once()

    def test_end_dispatch(self, input_):
        """End → 关闭补全 + 移到行尾。"""
        event = KeyEvent(kind="end")
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._end.assert_called_once()

    def test_delete_key_dispatch(self, input_):
        """Delete 键 (mod=0) → 关闭补全 + 删除 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=0)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._delete.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_ctrl_w_dispatch(self, input_):
        """Ctrl+W (mod=1) → 关闭补全 + 删词 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=1)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._delete_word_left.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_ctrl_u_dispatch(self, input_):
        """Ctrl+U (mod=2) → 关闭补全 + 删到行首 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=2)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._kill_to_bol.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()

    def test_ctrl_k_dispatch(self, input_):
        """Ctrl+K (mod=3) → 关闭补全 + 删到行尾 + 自动补全。"""
        event = KeyEvent(kind="delete", modifier=3)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._kill_to_eol.assert_called_once()
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
        input_._right.assert_called_once()

    def test_ctrl_right_dispatch(self, input_):
        """Ctrl+右 → _word_right。"""
        event = KeyEvent(kind="arrow_right", modifier=5)
        input_._dispatch_key_event(event)
        input_._word_right.assert_called_once()

    def test_arrow_left_dispatch(self, input_):
        """左箭头 → _left。"""
        event = KeyEvent(kind="arrow_left")
        input_._dispatch_key_event(event)
        input_._left.assert_called_once()

    def test_ctrl_left_dispatch(self, input_):
        """Ctrl+左 → _word_left。"""
        event = KeyEvent(kind="arrow_left", modifier=5)
        input_._dispatch_key_event(event)
        input_._word_left.assert_called_once()

    def test_char_newline_dispatch(self, input_):
        """CSI u Shift+Enter (kind=char, char='\n') → handle_char('\n')。"""
        event = KeyEvent(kind="char", char="\n")
        input_._dispatch_key_event(event)
        input_.handle_char.assert_called_once_with('\n')

    def test_unknown_captures(self, input_):
        """unknown → 关闭补全 + 捕获到 _captured_input。"""
        event = KeyEvent(kind="unknown", raw=b'\x00')
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        assert bytes(input_._captured_input) == b'\x00'

    def test_alt_backspace_dispatch(self, input_):
        """Alt+Backspace (backspace, mod=1) → 关闭补全 + 删词 + 自动补全。"""
        event = KeyEvent(kind="backspace", modifier=1)
        input_._dispatch_key_event(event)
        input_._dismiss_completion.assert_called_once()
        input_._delete_word_left.assert_called_once()
        input_._trigger_auto_completion.assert_called_once()
