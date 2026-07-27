"""InputParser 单元测试。

覆盖：ASCII 可打印字符 feed_byte、控制字符解码、
CSI 序列解析（箭头/Home/End/Delete/Ctrl+箭头/CSI u）、
SS3 序列、Alt+Backspace、双 Esc。

parse_escape_sequence 的 I/O 测试使用 os.pipe() 模拟。
"""

from __future__ import annotations

import pytest
import os
import select
from unittest.mock import patch, MagicMock

from src.tui.input._parser import InputParser, KeyEvent, _params_to_bytes


class TestFeedByteSimple:
    """feed_byte 简单字节测试（非 ESC）。"""

    def test_ascii_printable(self):
        parser = InputParser()
        ev = parser.feed_byte(ord('a'))
        assert ev is not None
        assert ev.kind == "char"
        assert ev.char == "a"
        assert ev.raw == b"a"

    def test_ascii_digit(self):
        parser = InputParser()
        ev = parser.feed_byte(ord('1'))
        assert ev is not None
        assert ev.kind == "char"
        assert ev.char == "1"

    def test_ascii_space(self):
        parser = InputParser()
        ev = parser.feed_byte(ord(' '))
        assert ev is not None
        assert ev.kind == "char"
        assert ev.char == " "

    def test_esc_returns_none(self):
        """ESC 字节返回 None，表示需要 parse_escape_sequence。"""
        parser = InputParser()
        assert parser.feed_byte(0x1b) is None


class TestFeedByteControlChars:
    """feed_byte 控制字符解码测试。"""

    def test_cr_enter(self):
        parser = InputParser()
        ev = parser.feed_byte(0x0d)
        assert ev.kind == "enter"

    def test_lf_enter(self):
        parser = InputParser()
        ev = parser.feed_byte(0x0a)
        assert ev.kind == "enter"

    def test_tab(self):
        parser = InputParser()
        ev = parser.feed_byte(0x09)
        assert ev.kind == "tab"

    def test_backspace_del(self):
        parser = InputParser()
        ev = parser.feed_byte(0x7f)
        assert ev.kind == "backspace"

    def test_backspace_bs(self):
        parser = InputParser()
        ev = parser.feed_byte(0x08)
        assert ev.kind == "backspace"

    def test_ctrl_c_interrupt(self):
        parser = InputParser()
        ev = parser.feed_byte(0x03)
        assert ev.kind == "interrupt"

    def test_ctrl_a_home(self):
        parser = InputParser()
        ev = parser.feed_byte(0x01)
        assert ev.kind == "home"

    def test_ctrl_e_end(self):
        parser = InputParser()
        ev = parser.feed_byte(0x05)
        assert ev.kind == "end"

    def test_ctrl_w_delete(self):
        parser = InputParser()
        ev = parser.feed_byte(0x17)
        assert ev.kind == "delete"
        assert ev.modifier == 1  # Ctrl+W indicator

    def test_ctrl_u_delete(self):
        parser = InputParser()
        ev = parser.feed_byte(0x15)
        assert ev.kind == "delete"
        assert ev.modifier == 2  # Ctrl+U indicator

    def test_ctrl_k_delete(self):
        parser = InputParser()
        ev = parser.feed_byte(0x0b)
        assert ev.kind == "delete"
        assert ev.modifier == 3  # Ctrl+K indicator

    def test_ctrl_g_special(self):
        parser = InputParser()
        ev = parser.feed_byte(0x07)
        assert ev.kind == "ctrl_key"
        assert ev.char == "\x07"

    def test_unknown_control(self):
        parser = InputParser()
        ev = parser.feed_byte(0x00)  # NUL
        assert ev.kind == "unknown"


class TestDispatchCSI:
    """_dispatch_csi 静态方法测试（纯逻辑，无 I/O）。"""

    def test_csi_up(self):
        ev = InputParser._dispatch_csi([], 'A')
        assert ev.kind == "arrow_up"

    def test_csi_down(self):
        ev = InputParser._dispatch_csi([], 'B')
        assert ev.kind == "arrow_down"

    def test_csi_left(self):
        ev = InputParser._dispatch_csi([], 'D')
        assert ev.kind == "arrow_left"

    def test_csi_right(self):
        ev = InputParser._dispatch_csi([], 'C')
        assert ev.kind == "arrow_right"

    def test_csi_home_h(self):
        ev = InputParser._dispatch_csi([], 'H')
        assert ev.kind == "home"

    def test_csi_end_f(self):
        ev = InputParser._dispatch_csi([], 'F')
        assert ev.kind == "end"

    def test_csi_home_tilde(self):
        ev = InputParser._dispatch_csi([1], '~')
        assert ev.kind == "home"

    def test_csi_end_tilde(self):
        ev = InputParser._dispatch_csi([4], '~')
        assert ev.kind == "end"

    def test_csi_delete_tilde(self):
        ev = InputParser._dispatch_csi([3], '~')
        assert ev.kind == "delete"

    def test_ctrl_left(self):
        ev = InputParser._dispatch_csi([1, 5], 'D')
        assert ev.kind == "arrow_left"
        assert ev.modifier == 5

    def test_ctrl_right(self):
        ev = InputParser._dispatch_csi([1, 5], 'C')
        assert ev.kind == "arrow_right"
        assert ev.modifier == 5

    def test_csi_u_shift_enter(self):
        ev = InputParser._dispatch_csi([13, 2], 'u')
        assert ev.kind == "char"
        assert ev.char == "\n"
        assert ev.modifier == 2

    def test_csi_u_alt_enter(self):
        ev = InputParser._dispatch_csi([13, 3], 'u')
        assert ev.kind == "char"
        assert ev.char == "\n"
        assert ev.modifier == 3

    def test_csi_u_unknown(self):
        ev = InputParser._dispatch_csi([65, 1], 'u')  # 'A' with no modifier
        assert ev.kind == "csi_u"
        assert ev.keycode == 65

    def test_csi_unknown_terminator(self):
        ev = InputParser._dispatch_csi([], 'X')
        assert ev.kind == "unknown"


class TestParseEscapeSequence:
    """parse_escape_sequence I/O 测试（使用 os.pipe + mock）。"""

    def test_arrow_up(self):
        """CSI A → arrow_up"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[A")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "arrow_up"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_arrow_down(self):
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[B")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "arrow_down"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_home(self):
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[H")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "home"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_end(self):
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[F")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "end"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_delete_tilde(self):
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[3~")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "delete"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_csi_u_shift_enter(self):
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[13;2u")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "char"
            assert ev.char == "\n"
            assert ev.modifier == 2
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_ss3_f1(self):
        """SS3 序列（ESC O P = F1）→ unknown"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"OP")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "unknown"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_alt_backspace(self):
        """ESC DEL → backspace with modifier=1"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x7f")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "backspace"
            assert ev.modifier == 1
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_double_esc(self):
        """双 ESC → interrupt"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x1b")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "interrupt"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_single_esc_timeout(self):
        """单 ESC（无后续字节）→ escape"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            # 不写任何数据 → select 超时
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "escape"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_other_esc_combination(self):
        """其他 ESC 组合 → interrupt"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"x")  # ESC x → some unknown combo
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "interrupt"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_ctrl_arrow_left(self):
        """Ctrl+左箭头 → arrow_left with modifier=5"""
        parser = InputParser()
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[1;5D")
            ev = parser.parse_escape_sequence(r_fd)
            assert ev.kind == "arrow_left"
            assert ev.modifier == 5
        finally:
            os.close(r_fd)
            os.close(w_fd)


class TestParseEscapeSequenceMock:
    """parse_escape_sequence 的 mock 测试（隔离真实的 fd I/O）。"""

    def test_select_error_returns_escape(self):
        """select 异常时返回 escape。"""
        parser = InputParser()
        with patch("select.select", side_effect=ValueError):
            ev = parser.parse_escape_sequence(0)
            assert ev.kind == "escape"

    def test_os_read_empty_returns_escape(self):
        """os.read 返回空时返回 escape。"""
        parser = InputParser()
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", return_value=b""):
                ev = parser.parse_escape_sequence(0)
                assert ev.kind == "escape"

    def test_os_read_error_returns_escape(self):
        """os.read 异常时返回 escape。"""
        parser = InputParser()
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", side_effect=OSError):
                ev = parser.parse_escape_sequence(0)
                assert ev.kind == "escape"


class TestKeyEvent:
    """KeyEvent 数据类测试。"""

    def test_defaults(self):
        ev = KeyEvent(kind="char")
        assert ev.char == ""
        assert ev.modifier == 0
        assert ev.keycode == 0
        assert ev.raw == b""

    def test_slots(self):
        """确认使用 __slots__（不可动态添加属性）。"""
        ev = KeyEvent(kind="char", char="x", modifier=2, keycode=13, raw=b"\x1b[13;2u")
        with pytest.raises(AttributeError):
            ev.extra = "value"


class TestParamsToBytes:
    """_params_to_bytes 辅助函数测试。"""

    def test_empty(self):
        assert _params_to_bytes([]) == b""

    def test_single(self):
        assert _params_to_bytes([13]) == b"13"

    def test_multiple(self):
        assert _params_to_bytes([13, 2]) == b"13;2"
