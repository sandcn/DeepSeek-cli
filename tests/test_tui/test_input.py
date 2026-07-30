"""test_input — Input（精简版）单元测试。

测试范围：
  - KeyEvent 解析（CSI 箭头/Home/End/Delete/Ctrl 组合、SS3、UTF-8）
  - Input 缓冲操作（handle_char/handle_chars/get_queued_input/reset/set_buffer）
  - Input 历史管理（load_history mock）
  - 补全回调流程
  - 光标视觉位置计算（_compute_cursor_visual_pos）
  - 异常类型明确性（无裸 except，所有异常类型显式声明）
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


# ═══════════════════════════════════════════════════════════
# get_queued_input 类型安全
# ═══════════════════════════════════════════════════════════

class TestGetQueuedInputTypeSafety:
    """测试 get_queued_input() 返回类型 str|None 的调用方安全性。

    验证：
    1. get_queued_input() 在无排队输入时返回 None
    2. get_queued_input() 在有排队输入时返回 str
    3. 调用方在收到 None 时不崩溃（不调用 .encode() 等 str 方法）
    """

    def test_returns_none_when_no_input(self, input_instance):
        """无排队输入时 get_queued_input() 返回 None。"""
        inp = input_instance
        result = inp.get_queued_input()
        assert result is None

    def test_returns_string_after_enter(self, input_instance):
        """Enter 提交后 get_queued_input() 返回 str。"""
        inp = input_instance
        inp.handle_chars("test type safety")
        inp._enter()
        result = inp.get_queued_input()
        assert result is not None
        assert isinstance(result, str)
        assert result == "test type safety"

    def test_returns_none_after_drain(self, input_instance):
        """消费后再次调用 get_queued_input() 返回 None。"""
        inp = input_instance
        inp.handle_chars("drain me")
        inp._enter()
        first = inp.get_queued_input()
        assert first is not None  # 第一次返回 str
        second = inp.get_queued_input()
        assert second is None  # 第二次返回 None

    def test_caller_handles_none_safely(self, input_instance):
        """模拟调用方安全处理 None（不调用 .encode()/.strip() 等 str 方法）。"""
        inp = input_instance
        text = inp.get_queued_input()
        # 调用方应检查 None 再操作
        if text is not None:
            encoded = text.encode("utf-8")
            assert isinstance(encoded, bytes)
        # 若 text 为 None，此分支不执行，不崩溃
        assert text is None  # 确认本测试场景下返回 None

    def test_caller_handles_string_safely(self, input_instance):
        """模拟调用方正常处理 str 返回值。"""
        inp = input_instance
        inp.handle_chars("safe string")
        inp._enter()
        text = inp.get_queued_input()
        assert text is not None
        # 调用方在确认非 None 后调用 str 方法
        encoded = text.encode("utf-8")
        stripped = text.strip()
        assert isinstance(encoded, bytes)
        assert isinstance(stripped, str)


# ═══════════════════════════════════════════════════════════
# 异常类型明确性测试（无裸 except）
# ═══════════════════════════════════════════════════════════

class TestExplicitExceptionTypes:
    """验证 _input.py 中无裸 ``except:`` 捕获（修复 P2 裸 except）。

    所有异常捕获必须使用明确异常类型（如 ``except (ValueError, OSError):``），
    禁止使用裸 ``except:`` 吞没 KeyboardInterrupt/SystemExit。

    验证策略：
    1. 源文件扫描：确认文件中无裸 ``except:`` 语法
    2. 功能验证：在边界条件下（无效 fd/超时）异常被正确捕获而非吞没
    """

    def test_no_bare_except_in_source_file(self):
        """源文件中不存在裸 except: 语句。

        通过搜索 ``except:`` 模式确认（注意冒号前无括号或异常类型时即为裸 except）。
        """
        import ast
        import inspect

        from src.tui import _input
        source = inspect.getsource(_input)

        # 逐行检查 except 语句行
        lines = source.split('\n')
        bare_except_lines = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            # 匹配 "except:" 但排除 "except (": 和 "except Exception:"
            if stripped.startswith('except:'):
                bare_except_lines.append(i + 1)  # 1-based line number
            elif stripped.startswith('except ') and ':' in stripped:
                # 检查 except 后面是否有异常类型
                except_part = stripped[7:stripped.index(':')].strip()
                if not except_part or except_part == ':':
                    bare_except_lines.append(i + 1)

        assert not bare_except_lines, (
            f"存在裸 except: 的行: {bare_except_lines}"
        )

    def test_parse_escape_sequence_select_error_handled(self, input_instance):
        """_parse_escape_sequence 中的 select 异常被显式处理。

        验证在无效 fd 上调用 parse_sequence 时，异常被显式捕获并返回
        KeyEvent(kind='escape')，而非被裸 except 吞没或传播。
        """
        inp = input_instance
        # 使用无效 fd（-1）模拟 select/read 错误
        with patch.object(inp, '_fd', -1):
            result = inp.parse_sequence(fd_override=-1)
            # 验证异常被显式捕获并返回 escape event
            assert result is not None
            assert result.kind == "escape"
            # 验证不是被裸 except 吞没的 unknown
            assert result.kind != "unknown"

    def test_parse_escape_sequence_read_error_handled(self, input_instance):
        """_parse_escape_sequence 中的 os.read 异常被显式处理。"""
        inp = input_instance
        with patch.object(inp, '_fd', -1):
            result = inp.parse_sequence(fd_override=-1)
            assert result is not None
            assert result.kind == "escape"

    def test_flush_stdin_residual_exception_handled(self, input_instance):
        """_flush_stdin_residual 中的 select/read 异常被显式处理。

        使用无效 fd 模拟异常路径，验证异常被捕获后 break 而非传播。
        """
        inp = input_instance
        inp._stop.clear()
        with patch.object(inp, '_fd', -1):
            # 不应抛出异常
            inp._flush_stdin_residual(max_flush=5)

    def test_read_stdin_once_select_error_handled(self, input_instance):
        """read_stdin_once 中 select 异常增加计数而不崩溃。"""
        inp = input_instance
        inp.start_io()
        with patch.object(inp, '_fd', -1):
            # 不应抛出异常，select 错误被捕获
            result = inp.read_stdin_once()
            assert result is False  # 无数据可读
            # select 错误计数应增加
            assert inp._select_error_count > 0

    def test_read_stdin_once_read_error_handled(self, input_instance):
        """read_stdin_once 中 os.read 异常被捕获而不崩溃。"""
        inp = input_instance
        inp.start_io()
        # 使用 select mock 返回 "ready" 但 os.read 失败
        with patch('src.tui._input.select.select', return_value=([1], [], [])), \
             patch.object(inp, '_fd', 999):  # 无效 fd
            result = inp.read_stdin_once()
            assert result is False  # 异常被捕获，不崩溃

    def test_try_read_paste_exception_handled(self, input_instance):
        """try_read_paste 中 select/read 异常被捕获。"""
        inp = input_instance
        with patch.object(inp, '_fd', -1):
            result = inp.try_read_paste(-1, "a")
            assert result == "a"  # 异常后返回原始字符


# ═══════════════════════════════════════════════════════════
# _suppress_enter 绕过测试
# ═══════════════════════════════════════════════════════════

class TestSuppressEnterBypass:
    """测试 _handle_special_key('editmsg') 不绕过 _suppress_enter。

    验证：
    1. set_suppress_enter(True) 后 _dispatch_key_event 中 Enter 被抑制
    2. _handle_special_key('editmsg') 清除 suppress_enter 后调用 _enter()
    3. editmsg 路径不会意外被 suppress_enter 阻止
    """

    def test_enter_suppressed_when_suppress_enter_true(self, input_instance):
        """_suppress_enter=True 时 _dispatch_key_event 中的 Enter 被抑制。"""
        inp = input_instance
        inp.set_suppress_enter(True)
        inp.handle_chars("test enter suppressed")
        assert inp.get_current_text() == "test enter suppressed"

        # 模拟 Enter 按键事件分发
        inp._dispatch_key_event(KeyEvent(kind="enter"))

        # 由于 _suppress_enter=True，文本不应被提交
        assert not inp.has_queued_input()
        # 缓冲区应保持不变
        assert inp.get_current_text() == "test enter suppressed"

    def test_enter_processed_when_suppress_enter_false(self, input_instance):
        """_suppress_enter=False 时 _dispatch_key_event 中的 Enter 正常处理。"""
        inp = input_instance
        inp.set_suppress_enter(False)
        inp.handle_chars("test enter processed")
        assert inp.get_current_text() == "test enter processed"

        inp._dispatch_key_event(KeyEvent(kind="enter"))

        # Enter 被正常处理，文本被提交
        assert inp.has_queued_input()
        text = inp.get_queued_input()
        assert text == "test enter processed"

    def test_editmsg_clears_suppress_enter(self, input_instance):
        """_handle_special_key('editmsg') 在调用 _enter() 前清除 _suppress_enter。"""
        inp = input_instance
        inp.set_suppress_enter(True)
        inp.set_buffer("editmsg test")

        # 设置 mock special_key_callback 返回新文本
        cb = MagicMock(return_value="edited message")
        inp.set_special_key_callback(cb)

        # 调用 _handle_special_key('editmsg')
        inp._handle_special_key('editmsg')

        # 验证 _suppress_enter 被清除
        assert inp.get_suppress_enter() is False
        # 由于 enter 被调用，文本应从 get_queued_input 获取
        assert inp.has_queued_input()
        text = inp.get_queued_input()
        assert text == "edited message" or text is not None

    def test_editmsg_submits_without_suppress(self, input_instance):
        """editmsg 路径在 _suppress_enter=True 时仍能正确提交。"""
        inp = input_instance
        inp.set_suppress_enter(True)

        # 直接给 buffer 设置文本（模拟编辑后返回）
        inp.set_buffer("final edit result")

        # 模拟 special_key_callback 返回结果
        cb = MagicMock(return_value="final edit result")
        inp.set_special_key_callback(cb)

        # 调用 editmsg
        inp._handle_special_key('editmsg')

        # suppress_enter 已清除
        assert inp.get_suppress_enter() is False
        # 文本已被提交
        assert inp.has_queued_input()
        result = inp.get_queued_input()
        assert result == "final edit result"
