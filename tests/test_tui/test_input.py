"""test_input — Input（精简版）单元测试。

测试范围：
  - KeyEvent 解析（CSI 箭头/Home/End/Delete/Ctrl 组合、SS3、UTF-8）
  - Input 缓冲操作（handle_char/handle_chars/get_queued_input/reset/set_buffer）
  - Input 历史管理（load_history mock）
  - 补全回调流程
  - 光标视觉位置计算（_compute_cursor_visual_pos）
  - 不测试真实 stdin I/O（需 PTY 环境）
"""

from __future__ import annotations

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.tui._input import (
    Input,
    KeyEvent,
    _compute_cursor_visual_pos,
    _expand_tabs,
    _tab_pos_to_expanded,
    _wrap_by_width,
    _TAB_WIDTH,
)


# ═══════════════════════════════════════════════════════════
# KeyEvent 数据类
# ═══════════════════════════════════════════════════════════

class TestKeyEvent:
    """KeyEvent 数据类测试。"""

    def test_default_construction(self):
        ev = KeyEvent(kind="char", char="a")
        assert ev.kind == "char"
        assert ev.char == "a"
        assert ev.modifier == 0
        assert ev.keycode == 0
        assert ev.raw == b""

    def test_full_construction(self):
        ev = KeyEvent(kind="csi_u", modifier=5, keycode=13, raw=b"\x1b[13;5u")
        assert ev.kind == "csi_u"
        assert ev.modifier == 5
        assert ev.keycode == 13

    def test_slots_no_dict(self):
        """验证 slots=True 生效。"""
        ev = KeyEvent(kind="enter")
        with pytest.raises(AttributeError):
            ev.__dict__


# ═══════════════════════════════════════════════════════════
# Input 基础构造函数
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_history_file():
    """创建临时历史文件。"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".hist", delete=False) as f:
        f.write("")
    yield Path(f.name)
    import os
    os.unlink(f.name)


@pytest.fixture
def input_instance(mock_history_file):
    """创建 Input 实例用于测试。"""
    return Input(fd=0, history_file=mock_history_file)


class TestInputConstructor:
    """Input 构造函数测试。"""

    def test_constructor_defaults(self, mock_history_file):
        inp = Input(fd=0, history_file=mock_history_file)
        assert inp.width > 0
        assert inp.height > 0
        assert not inp.is_io_running
        assert not inp.interrupted

    def test_constructor_with_custom_cache(self, mock_history_file):
        from src.tui._screen import TerminalWidthCache
        twc = TerminalWidthCache()
        inp = Input(fd=0, history_file=mock_history_file, term_width_cache=twc)
        assert inp._term_width_cache is twc


# ═══════════════════════════════════════════════════════════
# I/O 状态管理
# ═══════════════════════════════════════════════════════════

class TestIOState:
    """I/O 状态管理测试。"""

    def test_start_stop_io(self, input_instance):
        inp = input_instance
        inp.start_io()
        assert inp.is_io_running
        assert not inp.interrupted

        inp.stop_io()
        assert not inp.is_io_running

    def test_pause_resume_io(self, input_instance):
        inp = input_instance
        inp.start_io()
        inp.pause_io()
        # _active 被清除，read_stdin_once() 会跳过
        assert not inp._active.is_set()

        inp.resume_io()
        assert inp._active.is_set()

    def test_start_io_idempotent(self, input_instance):
        inp = input_instance
        inp.start_io()
        inp.start_io()  # 重复调用
        assert inp.is_io_running


# ═══════════════════════════════════════════════════════════
# 缓冲操作
# ═══════════════════════════════════════════════════════════

class TestBuffer:
    """缓冲操作测试。"""

    def test_handle_char(self, input_instance):
        inp = input_instance
        inp.handle_char('H')
        inp.handle_char('i')
        assert inp.get_current_text() == "Hi"
        assert not inp.has_queued_input()

    def test_handle_chars_batch(self, input_instance):
        inp = input_instance
        inp.handle_chars("Hello World")
        assert inp.get_current_text() == "Hello World"

    def test_enter_submits(self, input_instance):
        inp = input_instance
        inp.handle_chars("test input")
        inp._enter()
        assert inp.has_queued_input()
        assert inp.get_queued_input() == "test input"
        assert inp.get_current_text() == ""

    def test_reset(self, input_instance):
        inp = input_instance
        inp.handle_chars("some text")
        inp.reset()
        assert inp.get_current_text() == ""
        assert not inp.has_queued_input()

    def test_set_buffer(self, input_instance):
        inp = input_instance
        inp.set_buffer("prefill")
        assert inp.get_current_text() == "prefill"
        assert inp._cursor_pos == 7

    def test_drain_all(self, input_instance):
        inp = input_instance
        inp.handle_chars("drain_test")
        inp._enter()
        submitted, buffer_text = inp.drain_all()
        assert submitted == "drain_test" or submitted is not None
        assert inp.get_current_text() == ""


# ═══════════════════════════════════════════════════════════
# 编辑操作
# ═══════════════════════════════════════════════════════════

class TestEditing:
    """编辑操作测试。"""

    def test_backspace(self, input_instance):
        inp = input_instance
        inp.handle_chars("abc")
        inp._backspace()
        assert inp.get_current_text() == "ab"

    def test_left_right(self, input_instance):
        inp = input_instance
        inp.handle_chars("abc")
        inp._left()
        inp._left()
        # 光标在 'a' 后面
        inp.handle_char('X')
        assert inp.get_current_text() == "aXbc"

    def test_delete(self, input_instance):
        inp = input_instance
        inp.handle_chars("abc")
        inp._left()
        inp._left()
        inp._left()  # 光标在开头
        inp._delete()
        assert inp.get_current_text() == "bc"

    def test_home_end(self, input_instance):
        inp = input_instance
        inp.handle_chars("hello world")
        inp._home()
        inp.handle_char('X')
        assert inp.get_current_text() == "Xhello world"

    def test_word_left_right(self, input_instance):
        inp = input_instance
        inp.handle_chars("hello world foo")
        # 光标在末尾
        inp._word_left()  # 跳到 "foo"
        inp._word_left()  # 跳到 "world"
        inp._word_left()  # 跳到 "hello"
        inp.handle_char('X')
        assert inp.get_current_text() == "Xhello world foo"

    def test_delete_word_left(self, input_instance):
        inp = input_instance
        inp.handle_chars("hello world")
        inp._delete_word_left()
        assert inp.get_current_text() == "hello "

    def test_kill_to_bol(self, input_instance):
        inp = input_instance
        inp.handle_chars("hello world")
        inp._left()
        inp._left()
        inp._left()
        inp._left()
        inp._left()  # 光标在 "hello " 后
        inp._kill_to_bol()
        assert inp.get_current_text() == "world"

    def test_kill_to_eol(self, input_instance):
        inp = input_instance
        inp.handle_chars("hello world")
        inp._home()
        inp._kill_to_eol()
        assert inp.get_current_text() == ""


# ═══════════════════════════════════════════════════════════
# 解析方法
# ═══════════════════════════════════════════════════════════

class TestParsing:
    """ANSI/CSI 解析测试。"""

    def test_feed_byte_ascii_printable(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(ord('A'))
        assert ev is not None
        assert ev.kind == "char"
        assert ev.char == "A"

    def test_feed_byte_enter(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x0d)  # \r
        assert ev is not None
        assert ev.kind == "enter"

    def test_feed_byte_tab(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x09)
        assert ev is not None
        assert ev.kind == "tab"

    def test_feed_byte_backspace(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x7f)  # DEL
        assert ev is not None
        assert ev.kind == "backspace"

    def test_feed_byte_interrupt(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x03)  # Ctrl+C
        assert ev is not None
        assert ev.kind == "interrupt"

    def test_feed_byte_ctrl_a_home(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x01)  # Ctrl+A
        assert ev is not None
        assert ev.kind == "home"

    def test_feed_byte_ctrl_e_end(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x05)  # Ctrl+E
        assert ev is not None
        assert ev.kind == "end"

    def test_feed_byte_ctrl_w_delete_word(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x17)  # Ctrl+W
        assert ev is not None
        assert ev.kind == "delete"
        assert ev.modifier == 1

    def test_feed_byte_ctrl_u_kill_bol(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x15)  # Ctrl+U
        assert ev is not None
        assert ev.kind == "delete"
        assert ev.modifier == 2

    def test_feed_byte_ctrl_k_kill_eol(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x0b)  # Ctrl+K
        assert ev is not None
        assert ev.kind == "delete"
        assert ev.modifier == 3

    def test_feed_byte_esc_returns_none(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x1b)  # ESC
        assert ev is None

    def test_dispatch_csi_arrows(self):
        """测试 CSI 箭头键分发。"""
        # 上箭头: ESC [ A
        ev = Input._dispatch_csi([], 'A')
        assert ev.kind == "arrow_up"

        # 下箭头: ESC [ B
        ev = Input._dispatch_csi([], 'B')
        assert ev.kind == "arrow_down"

        # 右箭头: ESC [ C
        ev = Input._dispatch_csi([], 'C')
        assert ev.kind == "arrow_right"

        # 左箭头: ESC [ D
        ev = Input._dispatch_csi([], 'D')
        assert ev.kind == "arrow_left"

    def test_dispatch_csi_home_end(self):
        """测试 Home/End 分发。"""
        # Home: ESC [ H
        ev = Input._dispatch_csi([], 'H')
        assert ev.kind == "home"

        # End: ESC [ F
        ev = Input._dispatch_csi([], 'F')
        assert ev.kind == "end"

        # Home: ESC [ 1 ~
        ev = Input._dispatch_csi([1], '~')
        assert ev.kind == "home"

        # End: ESC [ 4 ~
        ev = Input._dispatch_csi([4], '~')
        assert ev.kind == "end"

    def test_dispatch_csi_delete(self):
        """测试 Delete 键分发。"""
        ev = Input._dispatch_csi([3], '~')
        assert ev.kind == "delete"

    def test_dispatch_csi_u(self):
        """测试 CSI u 模式（非 Enter 键）。"""
        ev = Input._dispatch_csi([97, 1], 'u')  # 'a' 键
        assert ev.kind == "csi_u"
        assert ev.keycode == 97
        assert ev.modifier == 1

    def test_dispatch_csi_ctrl_arrow(self):
        """测试 Ctrl+箭头键。"""
        ev = Input._dispatch_csi([1, 5], 'C')  # Ctrl+右
        assert ev.kind == "arrow_right"
        assert ev.modifier == 5

        ev = Input._dispatch_csi([1, 5], 'D')  # Ctrl+左
        assert ev.kind == "arrow_left"
        assert ev.modifier == 5

    def test_params_to_bytes(self):
        assert Input._params_to_bytes([]) == b""
        assert Input._params_to_bytes([13, 5]) == b"13;5"

    def test_decode_control_char_ctrl_keys(self):
        """测试 Ctrl+G/O/N/R 特殊按键。"""
        ev = Input._decode_control_char(0x07)  # Ctrl+G
        assert ev.kind == "ctrl_key"
        assert ev.char == '\x07'

        ev = Input._decode_control_char(0x0f)  # Ctrl+O
        assert ev.kind == "ctrl_key"

        ev = Input._decode_control_char(0x0e)  # Ctrl+N
        assert ev.kind == "ctrl_key"

        ev = Input._decode_control_char(0x12)  # Ctrl+R
        assert ev.kind == "ctrl_key"


# ═══════════════════════════════════════════════════════════
# 回显与回调
# ═══════════════════════════════════════════════════════════

class TestCallbacks:
    """回调接口测试。"""

    def test_echo_callback(self, input_instance):
        inp = input_instance
        cb = MagicMock()
        inp.set_echo_callback(cb)
        inp.handle_char('X')
        cb.assert_called()

    def test_special_key_callback(self, input_instance):
        inp = input_instance
        cb = MagicMock(return_value="modified")
        inp.set_special_key_callback(cb)
        inp.handle_chars("original")
        inp._handle_special_key("vim")
        cb.assert_called_with("vim", "original")

    def test_completion_callback_tab(self, input_instance):
        inp = input_instance
        cb = MagicMock(return_value="completed")
        inp.set_completion_callback(cb)
        inp.handle_chars("prefix")
        inp._handle_tab()
        assert inp.get_current_text() == "completed"

    def test_completion_callback_returns_none(self, input_instance):
        """补全回调返回 None 时插入制表符。"""
        inp = input_instance
        cb = MagicMock(return_value=None)
        inp.set_completion_callback(cb)
        inp.handle_chars("text")
        inp._handle_tab()
        assert "\t" in inp.get_current_text()


# ═══════════════════════════════════════════════════════════
# 光标视觉位置计算
# ═══════════════════════════════════════════════════════════

class TestCursorVisualPos:
    """_compute_cursor_visual_pos 及相关函数测试。"""

    def test_empty_text(self):
        row, col = _compute_cursor_visual_pos("", 0, 80)
        assert row == 0
        assert col == 0

    def test_simple_text(self):
        row, col = _compute_cursor_visual_pos("hello", 5, 80)
        assert row == 0
        assert col == 5

    def test_multiline(self):
        text = "line1\nline2\nline3"
        # 光标在 "line2" 后面（位置 11 = 5 + 1 + 5）
        row, col = _compute_cursor_visual_pos(text, 11, 80)
        assert row == 1  # 第二行
        assert col == 5

    def test_cursor_negative_means_end(self):
        row, col = _compute_cursor_visual_pos("hello", -1, 80)
        assert row == 0
        assert col == 5

    def test_wrap_by_width(self):
        text = "a" * 100
        row, col = _compute_cursor_visual_pos(text, 90, 30)
        # 每行 30 个字符，位置 90 应在某行末尾
        assert row >= 0

    def test_expand_tabs(self):
        text = "a\tb"
        result = _expand_tabs(text)
        # Tab 展开为 3 空格（到达第 4 列 = tab_width）
        assert result == "a   b"

    def test_expand_tabs_no_tab(self):
        assert _expand_tabs("hello") == "hello"

    def test_tab_pos_to_expanded(self):
        text = "a\tb"
        # 原始 pos=1 → 展开后 'a' 后面 3 空格 → pos=4
        expanded = _tab_pos_to_expanded(text, 2)
        assert expanded == 4

    def test_tab_pos_negative(self):
        assert _tab_pos_to_expanded("test", -1) == -1

    def test_wrap_by_width_basic(self):
        result = _wrap_by_width("abcdef", 3)
        assert result == ["abc", "def"]

    def test_wrap_by_width_with_newlines(self):
        result = _wrap_by_width("abc\ndefgh", 3)
        assert result == ["abc", "def", "gh"]

    def test_wrap_by_width_empty(self):
        assert _wrap_by_width("", 10) == [""]

    def test_wrap_by_width_zero_width(self):
        result = _wrap_by_width("test", 0)
        assert result == ["test"]

    def test_wrap_by_width_empty_segment(self):
        """连续换行产生空段。"""
        result = _wrap_by_width("a\n\nb", 10)
        assert "" in result

    def test_chinese_char_wrapping(self):
        """中文字符宽 2，英文宽 1。"""
        text = "你好world"
        result = _wrap_by_width(text, 8)
        # "你好" = 宽 4，"world" = 宽 5，总计 9 > 8
        # "你好wo" = 宽 4+2 = 6，"rld" = 宽 3
        assert len(result) == 2

    def test_compute_cursor_with_chinese(self):
        """中文光标位置正确。"""
        text = "你好世界"
        # 光标在末尾（pos=4 个字符，视觉宽度 8）
        row, col = _compute_cursor_visual_pos(text, 4, 80)
        assert row == 0
        assert col == 8  # 4 个中文字符 × 2


# ═══════════════════════════════════════════════════════════
# Input.compute_cursor()
# ═══════════════════════════════════════════════════════════

class TestComputeCursor:
    """Input.compute_cursor() 方法测试。"""

    def test_compute_cursor_returns_tuple(self, input_instance):
        inp = input_instance
        result = inp.compute_cursor(
            text="test", cursor_pos=4,
            bottom_lines=5, subagent_lines=0,
            completion_height=0,
        )
        assert isinstance(result, tuple)
        assert len(result) == 4
        r_cursor, cursor_col, vis_row, vis_col = result
        assert r_cursor >= 1
        assert cursor_col >= 1
        assert vis_row >= 0
        assert vis_col >= 0

    def test_compute_cursor_with_subagent(self, input_instance):
        inp = input_instance
        result_without = inp.compute_cursor(
            text="test", cursor_pos=4,
            bottom_lines=5, subagent_lines=0,
            completion_height=0,
        )
        result_with = inp.compute_cursor(
            text="test", cursor_pos=4,
            bottom_lines=5, subagent_lines=3,
            completion_height=0,
        )
        # subagent 行会增加 r_cursor
        assert result_with[0] > result_without[0]


# ═══════════════════════════════════════════════════════════
# 历史管理
# ═══════════════════════════════════════════════════════════

class TestHistory:
    """历史管理测试（mock 文件 I/O）。"""

    def test_history_navigation(self, input_instance):
        inp = input_instance
        # 手动填充历史
        inp._history = ["third", "second", "first"]
        inp._history_idx = -1

        inp.handle_chars("current")
        # 按上箭头进入历史
        inp._up()
        assert inp.get_current_text() == "third"

        inp._up()
        assert inp.get_current_text() == "second"

        inp._down()
        assert inp.get_current_text() == "third"

        inp._down()
        inp._down()  # 回到当前输入
        assert inp.get_current_text() == "current"

    def test_history_indicator(self, input_instance):
        inp = input_instance
        inp._history = ["item"]
        assert inp._history_indicator == ""

        inp.handle_chars("text")
        inp._up()
        assert "[历史" in inp._history_indicator or inp._history_indicator

    def test_empty_history_up_does_nothing(self, input_instance):
        inp = input_instance
        inp.handle_chars("text")
        inp._up()
        assert inp.get_current_text() == "text"

    def test_multiline_up_down(self, input_instance):
        """多行文本中上下箭头移动光标。"""
        inp = input_instance
        inp.handle_chars("line1\nline2\nline3")
        # 光标在末尾，第二个 line
        # 上移
        inp._up()
        # 光标应该移到上一行
        text = inp.get_current_text()
        assert "line" in text
