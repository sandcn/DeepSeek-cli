"""test_input_parser — InputParser（src/tui/_input_parser.py）单元测试。

覆盖方向⑤ 提取的 8 个解析方法边界：
  - feed_byte: 控制字符 / 可打印 / 高位字节 / ESC 返回 None
  - _decode_control_char: 各控制字符分支（静态）
  - _dispatch_csi: CSI u / 功能键 / 方向键 / 未知（静态）
  - _params_to_bytes: 空参 / 多参（静态）
  - _parse_escape_sequence / _read_csi_sequence / _read_ss3_sequence: I/O 边界（os.pipe 模拟）
  - parse_sequence: 入口（真实 pipe fd + mock select 异常路径）
"""

from __future__ import annotations

import os
from unittest.mock import patch

from src.tui._input_parser import InputParser, KeyEvent


def _pipe_input() -> tuple[int, int]:
    """创建用于解析测试的 pipe 对（r_fd, w_fd）。"""
    return os.pipe()


# ═══════════════════════════════════════════════════════════
# feed_byte — 单字节解析状态机
# ═══════════════════════════════════════════════════════════

class TestFeedByte:
    """feed_byte 边界（不依赖 fd，纯字节解析）。"""

    def setup_method(self):
        self.parser = InputParser()

    def test_esc_returns_none(self):
        """ESC (0x1b) → None（需要解析完整转义序列）。"""
        assert self.parser.feed_byte(0x1b) is None

    def test_ascii_printable(self):
        """可打印 ASCII → char 事件。"""
        ev = self.parser.feed_byte(ord('A'))
        assert ev is not None
        assert ev.kind == "char"
        assert ev.char == "A"
        assert ev.raw == b"A"

    def test_high_byte_utf8(self):
        """高位字节（UTF-8 首字节）→ char 事件（replace 解码）。"""
        ev = self.parser.feed_byte(0xe4)
        assert ev is not None
        assert ev.kind == "char"
        assert ev.raw == b"\xe4"

    def test_control_char_enter(self):
        """\r / \n → enter。"""
        ev = self.parser.feed_byte(0x0d)
        assert ev.kind == "enter"
        ev = self.parser.feed_byte(0x0a)
        assert ev.kind == "enter"

    def test_control_char_tab(self):
        """\t → tab。"""
        ev = self.parser.feed_byte(0x09)
        assert ev.kind == "tab"

    def test_control_char_backspace(self):
        """DEL / BS → backspace。"""
        ev = self.parser.feed_byte(0x7f)
        assert ev.kind == "backspace"
        ev = self.parser.feed_byte(0x08)
        assert ev.kind == "backspace"

    def test_control_char_interrupt(self):
        """Ctrl+C → interrupt。"""
        ev = self.parser.feed_byte(0x03)
        assert ev.kind == "interrupt"

    def test_control_char_unknown(self):
        """其他控制字符 → unknown。"""
        ev = self.parser.feed_byte(0x00)
        assert ev.kind == "unknown"


# ═══════════════════════════════════════════════════════════
# _decode_control_char — ASCII 控制字符解码（静态）
# ═══════════════════════════════════════════════════════════

class TestDecodeControlChar:
    """_decode_control_char 各分支。"""

    def test_ctrl_a_home(self):
        assert InputParser._decode_control_char(0x01).kind == "home"

    def test_ctrl_e_end(self):
        assert InputParser._decode_control_char(0x05).kind == "end"

    def test_ctrl_w_delete_word_left(self):
        ev = InputParser._decode_control_char(0x17)
        assert ev.kind == "delete"
        assert ev.modifier == 1

    def test_ctrl_u_kill_bol(self):
        ev = InputParser._decode_control_char(0x15)
        assert ev.kind == "delete"
        assert ev.modifier == 2

    def test_ctrl_k_kill_eol(self):
        ev = InputParser._decode_control_char(0x0b)
        assert ev.kind == "delete"
        assert ev.modifier == 3

    def test_ctrl_g_o_n_r_special(self):
        """Ctrl+G/O/N/R → ctrl_key（特殊按键）。"""
        for byte in (0x07, 0x0f, 0x0e, 0x12):
            ev = InputParser._decode_control_char(byte)
            assert ev.kind == "ctrl_key"
            assert ev.char == chr(byte)

    def test_unknown_control_char(self):
        ev = InputParser._decode_control_char(0x02)  # Ctrl+B
        assert ev.kind == "unknown"


# ═══════════════════════════════════════════════════════════
# _dispatch_csi — CSI 参数分发（静态）
# ═══════════════════════════════════════════════════════════

class TestDispatchCsi:
    """_dispatch_csi 各分支。"""

    def test_csi_u(self):
        ev = InputParser._dispatch_csi([97, 1], 'u')  # 'a' 键
        assert ev.kind == "csi_u"
        assert ev.keycode == 97
        assert ev.modifier == 1

    def test_csi_u_shift_enter(self):
        """CSI u Shift+Enter → char '\n'。"""
        ev = InputParser._dispatch_csi([13, 2], 'u')
        assert ev.kind == "char"
        assert ev.char == "\n"
        assert ev.modifier == 2

    def test_csi_u_shift_tab(self):
        """CSI u Shift+Tab（9;2）→ tab modifier=2（方向A 步骤1）。"""
        ev = InputParser._dispatch_csi([9, 2], 'u')
        assert ev.kind == "tab"
        assert ev.modifier == 2
        assert ev.keycode == 9

    def test_csi_u_ctrl_a_home(self):
        """CSI u Ctrl+A（97;5）→ home（复用控制字符语义，方向A 步骤1）。"""
        ev = InputParser._dispatch_csi([97, 5], 'u')
        assert ev.kind == "home"
        assert ev.keycode == 97

    def test_csi_u_ctrl_w_delete_word(self):
        """CSI u Ctrl+W（119;5）→ delete modifier=1（delete word left）。"""
        ev = InputParser._dispatch_csi([119, 5], 'u')
        assert ev.kind == "delete"
        assert ev.modifier == 1
        assert ev.keycode == 119

    def test_csi_u_other_kept(self):
        """其余 csi_u（非 Shift+Tab / Ctrl+字母）保持 csi_u（供 input router 消费）。"""
        ev = InputParser._dispatch_csi([97, 1], 'u')  # 普通 'a' 键
        assert ev.kind == "csi_u"
        assert ev.keycode == 97
        assert ev.modifier == 1

    def test_function_key_tilde(self):
        assert InputParser._dispatch_csi([1], '~').kind == "home"
        assert InputParser._dispatch_csi([7], '~').kind == "home"
        assert InputParser._dispatch_csi([3], '~').kind == "delete"
        assert InputParser._dispatch_csi([4], '~').kind == "end"
        assert InputParser._dispatch_csi([8], '~').kind == "end"

    def test_home_end(self):
        assert InputParser._dispatch_csi([], 'H').kind == "home"
        assert InputParser._dispatch_csi([], 'F').kind == "end"

    def test_arrows(self):
        assert InputParser._dispatch_csi([], 'A').kind == "arrow_up"
        assert InputParser._dispatch_csi([], 'B').kind == "arrow_down"
        assert InputParser._dispatch_csi([], 'C').kind == "arrow_right"
        assert InputParser._dispatch_csi([], 'D').kind == "arrow_left"

    def test_ctrl_arrows(self):
        ev = InputParser._dispatch_csi([1, 5], 'C')
        assert ev.kind == "arrow_right"
        assert ev.modifier == 5
        ev = InputParser._dispatch_csi([1, 5], 'D')
        assert ev.kind == "arrow_left"
        assert ev.modifier == 5

    def test_unknown(self):
        assert InputParser._dispatch_csi([], 'Z').kind == "unknown"


# ═══════════════════════════════════════════════════════════
# _params_to_bytes — CSI 参数字节串（静态）
# ═══════════════════════════════════════════════════════════

class TestParamsToBytes:
    """_params_to_bytes 边界。"""

    def test_empty(self):
        assert InputParser._params_to_bytes([]) == b""

    def test_multi(self):
        assert InputParser._params_to_bytes([13, 5]) == b"13;5"

    def test_single(self):
        assert InputParser._params_to_bytes([3]) == b"3"


# ═══════════════════════════════════════════════════════════
# parse_sequence / _parse_escape_sequence / _read_csi_sequence /
# _read_ss3_sequence — I/O 边界（os.pipe 模拟）
# ═══════════════════════════════════════════════════════════

class TestParseSequenceIO:
    """parse_sequence 入口（真实 pipe fd）。"""

    def setup_method(self):
        self.parser = InputParser()

    def test_arrow_up(self):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[A")
            assert self.parser.parse_sequence(r_fd).kind == "arrow_up"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_arrow_down(self):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[B")
            assert self.parser.parse_sequence(r_fd).kind == "arrow_down"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_home(self):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[H")
            assert self.parser.parse_sequence(r_fd).kind == "home"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_csi_u_shift_enter(self):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[13;2u")
            ev = self.parser.parse_sequence(r_fd)
            assert ev.kind == "char"
            assert ev.char == "\n"
            assert ev.modifier == 2
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_ss3_f1(self):
        """ESC O P → f1（F1 功能键）。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"OP")
            assert self.parser.parse_sequence(r_fd).kind == "f1"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_ss3_f2_f4(self):
        """ESC O Q/R/S → f2/f3/f4。"""
        for seq, kind in ((b"OQ", "f2"), (b"OR", "f3"), (b"OS", "f4")):
            r_fd, w_fd = os.pipe()
            try:
                os.write(w_fd, seq)
                assert self.parser.parse_sequence(r_fd).kind == kind
            finally:
                os.close(r_fd)
                os.close(w_fd)

    def test_alt_backspace(self):
        """ESC DEL → backspace modifier=1。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x7f")
            ev = self.parser.parse_sequence(r_fd)
            assert ev.kind == "backspace"
            assert ev.modifier == 1
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_double_esc(self):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x1b")
            assert self.parser.parse_sequence(r_fd).kind == "interrupt"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_single_esc_timeout(self):
        r_fd, w_fd = os.pipe()
        try:
            assert self.parser.parse_sequence(r_fd).kind == "escape"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_other_esc_combination(self):
        """ESC+可打印字符 → alt_char（方向A 步骤1：不再 interrupt）。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"x")
            ev = self.parser.parse_sequence(r_fd)
            assert ev.kind == "alt_char"
            assert ev.char == "x"
            assert ev.modifier == 3
            assert ev.raw == b"\x1bx"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_esc_non_printable_still_interrupt(self):
        """ESC+非打印字符 → 仍 interrupt（旧语义保留）。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x01")  # ESC + Ctrl+A（非可打印）
            assert self.parser.parse_sequence(r_fd).kind == "interrupt"
        finally:
            os.close(r_fd)
            os.close(w_fd)


class TestReadCsiSequenceDirect:
    """_read_csi_sequence 直接调用（ESC [ 已消费）。"""

    def test_arrow_right(self):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[1;5C")
            parser = InputParser()
            ev = parser._read_csi_sequence(r_fd)
            assert ev.kind == "arrow_right"
            assert ev.modifier == 5
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_no_terminator(self):
        """无终结符 → unknown raw b'\\x1b['。"""
        r_fd, w_fd = os.pipe()
        try:
            parser = InputParser()
            ev = parser._read_csi_sequence(r_fd)
            assert ev.kind == "unknown"
            assert ev.raw == b"\x1b["
        finally:
            os.close(r_fd)
            os.close(w_fd)


class TestReadSs3SequenceDirect:
    """_read_ss3_sequence 直接调用（ESC O 已消费）。"""

    def test_f2(self):
        """SS3 Q → f2（方向A 步骤1：不再 unknown）。"""
        r_fd, w_fd = os.pipe()
        try:
            # _read_ss3_sequence 调用时 ESC O 已消费，fd 上应为 SS3 后的字符
            os.write(w_fd, b"Q")
            parser = InputParser()
            ev = parser._read_ss3_sequence(r_fd)
            assert ev.kind == "f2"
            assert ev.raw == b"\x1bOQ"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_unknown_ss3_keeps_raw(self):
        """未知 SS3 字符保持 unknown 且 raw 完整。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"X")
            parser = InputParser()
            ev = parser._read_ss3_sequence(r_fd)
            assert ev.kind == "unknown"
            assert ev.raw == b"\x1bOX"
        finally:
            os.close(r_fd)
            os.close(w_fd)


class TestParseEscapeSequenceMock:
    """_parse_escape_sequence 异常路径（全局 patch select.select）。"""

    def test_select_error_returns_escape(self):
        parser = InputParser()
        with patch("select.select", side_effect=ValueError):
            ev = parser._parse_escape_sequence(0)
            assert ev.kind == "escape"
            assert ev.raw == b"\x1b"


# ═══════════════════════════════════════════════════════════
# Input 委托链一致性（方向⑤ 组合持有）
# ═══════════════════════════════════════════════════════════

class TestInputDelegation:
    """Input 组合持有 InputParser，委托方法与直接调用一致。"""

    def test_input_delegates_feed_byte(self, tmp_path):
        from src.tui._input import Input
        inp = Input(fd=0, history_file=tmp_path / "h")
        ev = inp.feed_byte(0x0d)
        assert ev.kind == "enter"

    def test_input_static_forward(self, tmp_path):
        """Input._dispatch_csi/_params_to_bytes/_decode_control_char 转发等价。"""
        from src.tui._input import Input
        assert Input._dispatch_csi([], 'A').kind == "arrow_up"
        assert Input._params_to_bytes([13, 5]) == b"13;5"
        assert Input._decode_control_char(0x03).kind == "interrupt"

    def test_parser_attr_exposed(self, tmp_path):
        """Input._parser 为 InputParser 实例（组合持有）。"""
        from src.tui._input import Input
        from src.tui._input_parser import InputParser as _Parser
        inp = Input(fd=0, history_file=tmp_path / "h")
        assert isinstance(inp._parser, _Parser)
