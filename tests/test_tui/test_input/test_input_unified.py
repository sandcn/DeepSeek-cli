"""test_input_unified — 新统一 Input 类的单元测试。

覆盖 KeyEvent dataclass、解析器（_feed_byte / _dispatch_csi）、
缓冲操作、光标计算、I/O 生命周期等核心路径。
"""

from __future__ import annotations

import os

import pytest

from src.tui.input import Input, KeyEvent


# ═══════════════════════════════════════════════════════════
# KeyEvent dataclass 测试
# ═══════════════════════════════════════════════════════════

class TestKeyEvent:
    """KeyEvent dataclass 不变性测试。"""

    def test_default_fields(self):
        ev = KeyEvent(kind="char")
        assert ev.kind == "char"
        assert ev.char == ""
        assert ev.modifier == 0
        assert ev.keycode == 0
        assert ev.raw == b""

    def test_full_fields(self):
        ev = KeyEvent(kind="csi_u", modifier=2, keycode=13, raw=b"\x1b[13;2u")
        assert ev.kind == "csi_u"
        assert ev.char == ""
        assert ev.modifier == 2
        assert ev.keycode == 13
        assert ev.raw == b"\x1b[13;2u"

    def test_repr(self):
        ev = KeyEvent(kind="enter", char="\r", raw=b"\r")
        r = repr(ev)
        assert "enter" in r


class TestProcessEvents:
    """测试 process_events 委托 read_stdin_once()。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_process_events_no_data(self, inp):
        """空队列（无 stdin 数据）时 process_events 不抛异常。"""
        inp.process_events()  # 空队列不应抛异常

    def test_process_events_with_pipe(self, tmp_path):
        """process_events 通过 read_stdin_once() 从 pipe 读取并分发。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            os.write(w_fd, b"a")
            inp.process_events()
            assert inp.get_current_text() == "a"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_process_events_with_enter(self, tmp_path):
        """process_events 通过 read_stdin_once() 处理 Enter。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("test")
            os.write(w_fd, b"\r")
            inp.process_events()
            assert inp.has_queued_input()
            assert inp.get_queued_input() == "test"
        finally:
            os.close(w_fd)
            os.close(r_fd)

class TestFeedByte:
    """测试 Input.feed_byte() 单字节解析。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_esc_returns_none(self, inp):
        assert inp.feed_byte(0x1b) is None

    def test_enter(self, inp):
        ev = inp.feed_byte(0x0d)
        assert ev is not None
        assert ev.kind == "enter"

    def test_tab(self, inp):
        ev = inp.feed_byte(0x09)
        assert ev is not None
        assert ev.kind == "tab"

    def test_backspace(self, inp):
        ev = inp.feed_byte(0x7f)
        assert ev is not None
        assert ev.kind == "backspace"

    def test_ctrl_c_interrupt(self, inp):
        ev = inp.feed_byte(0x03)
        assert ev is not None
        assert ev.kind == "interrupt"

    def test_ctrl_a_home(self, inp):
        ev = inp.feed_byte(0x01)
        assert ev is not None
        assert ev.kind == "home"

    def test_ctrl_e_end(self, inp):
        ev = inp.feed_byte(0x05)
        assert ev is not None
        assert ev.kind == "end"

    def test_ctrl_w_delete_word(self, inp):
        ev = inp.feed_byte(0x17)
        assert ev is not None
        assert ev.kind == "delete"
        assert ev.modifier == 1

    def test_ctrl_u_kill_bol(self, inp):
        ev = inp.feed_byte(0x15)
        assert ev is not None
        assert ev.kind == "delete"
        assert ev.modifier == 2

    def test_ctrl_k_kill_eol(self, inp):
        ev = inp.feed_byte(0x0b)
        assert ev is not None
        assert ev.kind == "delete"
        assert ev.modifier == 3

    def test_ctrl_g_special(self, inp):
        ev = inp.feed_byte(0x07)
        assert ev is not None
        assert ev.kind == "ctrl_key"
        assert ev.char == '\x07'

    def test_printable_char(self, inp):
        ev = inp.feed_byte(ord('a'))
        assert ev is not None
        assert ev.kind == "char"
        assert ev.char == "a"

    def test_unknown_control(self, inp):
        ev = inp.feed_byte(0x00)  # NUL
        assert ev is not None
        assert ev.kind == "unknown"


# ═══════════════════════════════════════════════════════════
# _decode_control_char 静态方法测试
# ═══════════════════════════════════════════════════════════

class TestDecodeControlChar:
    """测试 Input._decode_control_char() 静态方法。"""

    def test_lf_enter(self):
        ev = Input._decode_control_char(0x0a)
        assert ev.kind == "enter"

    def test_cr_enter(self):
        ev = Input._decode_control_char(0x0d)
        assert ev.kind == "enter"

    def test_backspace_del(self):
        ev = Input._decode_control_char(0x7f)
        assert ev.kind == "backspace"

    def test_backspace_bs(self):
        ev = Input._decode_control_char(0x08)
        assert ev.kind == "backspace"


# ═══════════════════════════════════════════════════════════
# _dispatch_csi 静态方法测试
# ═══════════════════════════════════════════════════════════

class TestDispatchCsi:
    """测试 Input._dispatch_csi() 静态方法。"""

    def test_arrow_up(self):
        ev = Input._dispatch_csi([], 'A')
        assert ev.kind == "arrow_up"

    def test_arrow_down(self):
        ev = Input._dispatch_csi([], 'B')
        assert ev.kind == "arrow_down"

    def test_arrow_right(self):
        ev = Input._dispatch_csi([], 'C')
        assert ev.kind == "arrow_right"

    def test_arrow_left(self):
        ev = Input._dispatch_csi([], 'D')
        assert ev.kind == "arrow_left"

    def test_ctrl_right(self):
        ev = Input._dispatch_csi([1, 5], 'C')
        assert ev.kind == "arrow_right"
        assert ev.modifier == 5

    def test_ctrl_left(self):
        ev = Input._dispatch_csi([1, 5], 'D')
        assert ev.kind == "arrow_left"
        assert ev.modifier == 5

    def test_home_csi(self):
        ev = Input._dispatch_csi([], 'H')
        assert ev.kind == "home"

    def test_end_csi(self):
        ev = Input._dispatch_csi([], 'F')
        assert ev.kind == "end"

    def test_home_tilde(self):
        ev = Input._dispatch_csi([1], '~')
        assert ev.kind == "home"

    def test_end_tilde(self):
        ev = Input._dispatch_csi([4], '~')
        assert ev.kind == "end"

    def test_delete_tilde(self):
        ev = Input._dispatch_csi([3], '~')
        assert ev.kind == "delete"

    def test_csi_u(self):
        ev = Input._dispatch_csi([65, 2], 'u')
        assert ev.kind == "csi_u"
        assert ev.keycode == 65
        assert ev.modifier == 2

    def test_csi_u_shift_enter(self):
        ev = Input._dispatch_csi([13, 2], 'u')
        assert ev.kind == "char"
        assert ev.char == "\n"
        assert ev.modifier == 2

    def test_unknown_csi(self):
        ev = Input._dispatch_csi([], 'Z')
        assert ev.kind == "unknown"


# ═══════════════════════════════════════════════════════════
# _params_to_bytes 静态方法测试
# ═══════════════════════════════════════════════════════════

class TestParamsToBytes:
    """测试 Input._params_to_bytes() 静态方法。"""

    def test_empty(self):
        assert Input._params_to_bytes([]) == b""

    def test_single(self):
        assert Input._params_to_bytes([1]) == b"1"

    def test_multiple(self):
        assert Input._params_to_bytes([1, 5]) == b"1;5"


# ═══════════════════════════════════════════════════════════
# 缓冲操作测试（原 InputBuffer）
# ═══════════════════════════════════════════════════════════

class TestBufferOperations:
    """测试内联缓冲操作。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_handle_char(self, inp):
        inp.handle_char('h')
        inp.handle_char('i')
        assert inp.get_current_text() == "hi"

    def test_handle_chars(self, inp):
        inp.handle_chars("hello world")
        assert inp.get_current_text() == "hello world"

    def test_backspace(self, inp):
        inp.handle_chars("hello")
        inp._backspace()
        assert inp.get_current_text() == "hell"

    def test_left_right(self, inp):
        inp.handle_chars("abc")
        inp._left()
        inp.handle_char('X')
        assert inp.get_current_text() == "abXc"

    def test_enter_submit(self, inp):
        inp.handle_chars("test")
        inp._enter()
        assert inp.has_queued_input()
        assert inp.get_queued_input() == "test"
        assert inp.get_current_text() == ""

    def test_enter_idempotent(self, inp):
        inp.handle_chars("test")
        inp._enter()
        inp._enter()  # 第二次 Enter 不应覆盖
        assert inp.get_queued_input() == "test"
        assert inp.has_queued_input() is False

    def test_home(self, inp):
        inp.handle_chars("hello")
        inp._left()
        inp._left()
        inp._home()
        inp.handle_char('X')
        assert inp.get_current_text() == "Xhello"

    def test_end(self, inp):
        inp.handle_chars("hello")
        inp._home()
        inp._end()
        inp.handle_char('!')
        assert inp.get_current_text() == "hello!"

    def test_delete(self, inp):
        inp.handle_chars("hello")
        inp._home()
        inp._delete()
        assert inp.get_current_text() == "ello"

    def test_delete_word_left(self, inp):
        inp.handle_chars("hello world")
        inp._delete_word_left()
        assert inp.get_current_text() == "hello "

    def test_kill_to_bol(self, inp):
        inp.handle_chars("hello world")
        inp._left()
        inp._left()
        inp._left()
        inp._left()
        inp._left()
        inp._kill_to_bol()
        assert inp.get_current_text() == "world"

    def test_kill_to_eol(self, inp):
        inp.handle_chars("hello world")
        inp._home()
        inp._right()
        inp._right()
        inp._right()
        inp._right()
        inp._right()
        inp._kill_to_eol()
        assert inp.get_current_text() == "hello"

    def test_word_left(self, inp):
        inp.handle_chars("hello world")
        inp._word_left()
        inp.handle_char('X')
        assert inp.get_current_text() == "hello Xworld"

    def test_word_right(self, inp):
        inp.handle_chars("hello world")
        inp._home()
        inp._word_right()
        inp.handle_char('X')
        # readline 行为：跳过 hello → 跳过空格 → 停在 world 的 'w'
        assert inp.get_current_text() == "hello Xworld"

    def test_reset(self, inp):
        inp.handle_chars("hello")
        inp.reset()
        assert inp.get_current_text() == ""
        assert inp.has_queued_input() is False

    def test_drain_all(self, inp):
        inp.handle_chars("buffer_text")
        inp._enter()
        submitted, buf = inp.drain_all()
        assert submitted == "buffer_text"
        assert buf == ""
        assert inp.get_current_text() == ""

    def test_drain_all_no_submit(self, inp):
        inp.handle_chars("buffer_text")
        submitted, buf = inp.drain_all()
        assert submitted is None
        assert buf == "buffer_text"

    def test_set_buffer(self, inp):
        inp.set_buffer("prefill")
        assert inp.get_current_text() == "prefill"

    def test_echo_callback(self, inp):
        calls = []
        inp.set_echo_callback(lambda text, pos: calls.append((text, pos)))
        inp.handle_char('a')
        assert len(calls) == 1
        assert calls[0][0] == "a"
        assert calls[0][1] == 1

    def test_history_navigation_up_down(self, inp):
        """测试上/下箭头历史导航（手动设置历史列表）。"""
        inp._history = ["line3", "line2", "line1"]
        inp.set_buffer("current")
        inp._up()
        # 进入历史导航 → 应显示最近的历史
        assert inp.get_current_text() in ("line3", "line2", "line1")
        assert inp._history_idx >= 0

    def test_multiline_up_down(self, inp):
        """测试多行输入中的上/下箭头（行内移动，非历史导航）。"""
        inp.handle_chars("line1\nline2\nline3")
        # 光标在末尾，上箭头应上移一行
        inp._up()
        # 光标应在 "line2" 的末尾附近
        text = inp.get_current_text()
        assert text == "line1\nline2\nline3"
        # 上移一行后 _up() 再按一次进入历史
        inp._up()
        assert inp.get_current_text() == "line1\nline2\nline3"  # 仍在行内移动

    def test_multiline_home_end(self, inp):
        """测试多行文本中的 Home/End。"""
        inp.handle_chars("abc\ndef\nghi")
        inp._home()
        inp.handle_char('X')
        # Home 移到当前逻辑行首（最后一行 = "ghi"）
        # 光标最初在末尾，Home 移到 "ghi" 行首
        assert "Xghi" in inp.get_current_text()

    def test_interrupted_flag_reset_by_reset(self, inp):
        assert inp.interrupted is False
        inp._interrupted.set()
        assert inp.interrupted is True
        inp.reset()
        assert inp.interrupted is False


# ═══════════════════════════════════════════════════════════
# 回调接口测试
# ═══════════════════════════════════════════════════════════

class TestCallbacks:
    """测试回调注册和调用。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_special_key_callback(self, inp):
        results = []
        inp.set_special_key_callback(lambda a, t: results.append((a, t)) or t)
        # 验证 _handle_special_key 直接调用回调（不操作 _active）
        active_before = inp._active.is_set()
        inp._handle_special_key('vim')
        active_after = inp._active.is_set()
        assert len(results) == 1
        assert results[0][0] == 'vim'
        # _active 标志在回调前后不变（不再有暂停/恢复逻辑）
        assert active_before == active_after

    def test_completion_callback(self, inp):
        inp.set_completion_callback(lambda t: t + "_completed")
        inp.handle_chars("hello")
        inp._handle_tab()
        assert inp.get_current_text() == "hello_completed"

    def test_completion_callback_none_fallback(self, inp):
        inp.set_completion_callback(lambda t: None)
        inp.handle_chars("hello")
        inp._handle_tab()
        # None 返回 → 插入制表符
        assert "\t" in inp.get_current_text()

    def test_completion_navigate(self, inp):
        inp.set_completion_navigate_callback(lambda d, t: t + "_nav")
        inp.handle_chars("prefix")
        inp._handle_arrow_up()
        assert inp.get_current_text() == "prefix_nav"

    def test_auto_completion(self, inp):
        results = []
        inp.set_auto_completion_callback(lambda t: results.append(t))
        inp.handle_char('a')
        inp._trigger_auto_completion()
        assert len(results) == 1
        assert results[0] == "a"

    def test_dismiss_completion(self, inp):
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp._dismiss_completion()
        assert len(dismissed) == 1


# ═══════════════════════════════════════════════════════════
# 光标计算测试
# ═══════════════════════════════════════════════════════════

class TestComputeCursor:
    """测试 Input.compute_cursor()。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            yield inp
        finally:
            os.close(fd)

    def test_empty_text(self, inp):
        r, c, vr, vc = inp.compute_cursor("", 0, 0, 0, 0)
        assert r >= 1
        assert c >= 1
        assert vr == 0

    def test_simple_text(self, inp):
        r, c, vr, vc = inp.compute_cursor("hello", 5, 0, 0, 0)
        assert vr >= 0
        assert vc >= 0


# ═══════════════════════════════════════════════════════════
# I/O 线程测试
# ═══════════════════════════════════════════════════════════

class TestIOThread:
    """测试 start_io / stop_io / pause_io / resume_io（标志位管理模式）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            yield inp
        finally:
            os.close(fd)

    def test_is_io_running_initially_false(self, inp):
        assert inp.is_io_running is False

    def test_start_stop_io(self, inp):
        inp.start_io()
        assert inp.is_io_running is True
        inp.stop_io()
        assert inp.is_io_running is False

    def test_stop_io_idempotent(self, inp):
        inp.stop_io()  # 未启动时调用应安全
        assert inp.is_io_running is False

    def test_pause_resume_io(self, inp):
        inp.start_io()
        inp.pause_io()
        assert not inp._active.is_set()
        inp.resume_io()
        assert inp._active.is_set()
        inp.stop_io()


# ═══════════════════════════════════════════════════════════
# _unescape 静态方法测试
# ═══════════════════════════════════════════════════════════

class TestUnescape:
    def test_no_escape(self):
        assert Input._unescape("hello") == "hello"

    def test_escaped_newline(self):
        assert Input._unescape(r"hello\nworld") == "hello\nworld"

    def test_multiple_newlines(self):
        assert Input._unescape(r"a\nb\nc") == "a\nb\nc"

    def test_empty(self):
        assert Input._unescape("") == ""


# ═══════════════════════════════════════════════════════════
# 属性测试
# ═══════════════════════════════════════════════════════════

class TestProperties:
    """测试 width/height/interrupted 属性。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_width(self, inp):
        w = inp.width
        assert isinstance(w, int)
        assert w > 0

    def test_height(self, inp):
        h = inp.height
        assert isinstance(h, int)
        assert h > 0

    def test_interrupted_default(self, inp):
        assert inp.interrupted is False

    def test_get_history_indicator_empty(self, inp):
        assert inp.get_history_indicator() == ""

    def test_capture_bytes_and_drain(self, inp):
        inp.capture_bytes(b"\xff\xfe")
        assert inp.drain_captured() != ""
        assert inp.drain_captured() == ""  # 已清空
