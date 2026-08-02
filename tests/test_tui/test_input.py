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

import os
import select
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
        assert submitted == "drain_test"
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

    def test_feed_byte_ctrl_e_ctrl_key(self, input_instance):
        inp = input_instance
        ev = inp.feed_byte(0x05)  # Ctrl+E（方向1 B1：不再 end）
        assert ev is not None
        assert ev.kind == "ctrl_key"
        assert ev.char == "\x05"

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
        """测试 CSI u 模式（非 Enter 键）。

        方向1 修复：可打印 ASCII keycode（如 'a'=97）→ char 事件（增强键盘
        终端正常打字）——旧实现落入 csi_u no-op 被静默丢弃。
        """
        ev = Input._dispatch_csi([97, 1], 'u')  # 'a' 键
        assert ev.kind == "char"
        assert ev.char == "a"
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
        # 100 字符按 30 列拆为 4 行（30/30/30/10），
        # 位置 90 位于第 3 行（0-based 2）的列 30（行末）
        assert row == 2
        assert col == 30

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
        assert inp._history_indicator == " [历史 1/1]"

    def test_empty_history_up_does_nothing(self, input_instance):
        inp = input_instance
        inp.handle_chars("text")
        inp._up()
        assert inp.get_current_text() == "text"

    def test_multiline_up_down(self, input_instance):
        """多行文本中上下箭头移动光标。"""
        inp = input_instance
        inp.handle_chars("line1\nline2\nline3")
        # 光标在末尾（pos=17，"line3" 之后）
        assert inp._cursor_pos == 17
        inp._up()
        # 光标应移到上一行 "line2" 末尾（pos=11）
        assert inp._cursor_pos == 11


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
        """源文件中不存在裸 except: 语句（P1-1：覆盖四个拆分文件）。

        方向A 步骤1 拆分后，Input 上帝类拆分为 _input.py / _input_io.py /
        _input_buffer.py / _input_dispatcher.py 四个文件；本测试扩展扫描范围
        至全部四个文件，保持无裸 except 断言。
        """
        import ast
        import inspect

        from src.tui import _input
        # P1-1：扩展扫描范围至 _input*.py 全部拆分文件
        # （_input.py 模块级已导入 _input_io/_input_buffer/_input_dispatcher）
        from src.tui import _input_io, _input_buffer, _input_dispatcher
        modules = [_input, _input_io, _input_buffer, _input_dispatcher]

        bare_except_lines: list[str] = []
        for mod in modules:
            source = inspect.getsource(mod)
            lines = source.split('\n')
            for i, line in enumerate(lines):
                stripped = line.strip()
                # 匹配 "except:" 但排除 "except (": 和 "except Exception:"
                if stripped.startswith('except:'):
                    bare_except_lines.append(f"{mod.__name__}:{i + 1}")
                elif stripped.startswith('except ') and ':' in stripped:
                    # 检查 except 后面是否有异常类型
                    except_part = stripped[7:stripped.index(':')].strip()
                    if not except_part or except_part == ':':
                        bare_except_lines.append(f"{mod.__name__}:{i + 1}")

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
        assert text == "edited message"

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


# ═══════════════════════════════════════════════════════════
# editmsg 残留 Enter 标记与 LF 丢弃回归测试（新增，2026-08-01）
# ═══════════════════════════════════════════════════════════

class TestEnterResidualLF:
    """editmsg 选择确认后残留 LF 丢弃回归测试（修复 CR+LF 竞态）。

    终端按 Enter 发送 CR+LF：CR 在 _suppress_enter=True 期间被
    _dispatch_key_event 抑制（消费选择确认），残留 LF 若在 prefill 注入
    之后才被 render 线程处理，会被 _enter() 误提交。本类验证修复 1：
    - 被抑制 Enter 后置 _enter_residual_pending，紧随 LF 被
      read_stdin_once 丢弃（不触发 _enter，prefill 保持可编辑）；
    - set_suppress_enter(False) 清残留标记，单 CR 终端恢复后用户
      后续 Enter 不被误丢弃。
    """

    def test_enter_residual_lf_discarded_regression(
        self, tmp_path, wait_pipe_readable_fixture,
    ) -> None:
        """被抑制 Enter 后紧随 LF 被丢弃（不触发 _enter，prefill 保持可编辑）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "test_history")
            inp.start_io()
            inp.set_suppress_enter(True)
            inp.handle_chars("edit content")
            os.write(w_fd, b"\r\n")

            # 第一次 read_stdin_once：处理 CR（被抑制）→ 置残留标记
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp._dispatcher._enter_residual_pending is True
            assert not inp.has_queued_input()
            assert inp.get_current_text() == "edit content"

            # 第二次 read_stdin_once：处理 LF → 被丢弃（不触发 _enter）
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp._dispatcher._enter_residual_pending is False
            assert not inp.has_queued_input()
            assert inp.get_current_text() == "edit content"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_suppress_false_clears_residual_enter_commits_regression(self, tmp_path) -> None:
        """set_suppress_enter(False) 清残留标记，用户后续 Enter 正常提交。

        覆盖「单 CR 终端（无 LF）恢复后用户 Enter 不被误丢弃」。
        """
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "test_history")
            inp.set_suppress_enter(True)
            inp.handle_chars("edit content")
            # 模拟被抑制 Enter（CR 被 _dispatch_key_event 消费）
            inp._dispatch_key_event(KeyEvent(kind="enter"))
            assert inp._dispatcher._enter_residual_pending is True
            assert not inp.has_queued_input()

            # 恢复 Enter 抑制 → 清残留标记
            inp.set_suppress_enter(False)
            assert inp._dispatcher._enter_residual_pending is False

            # 用户后续 Enter 正常提交（不被误丢弃）
            with patch("src.tui._input._append_to_history_file", return_value=True):
                inp._dispatch_key_event(KeyEvent(kind="enter"))
            assert inp.has_queued_input()
            assert inp.get_queued_input() == "edit content"
        finally:
            os.close(w_fd)
            os.close(r_fd)


# ═══════════════════════════════════════════════════════════
# 以下类合并自 test_input/ 目录（步骤 12 Input 测试去重）
# 原文件：test_input_unified / test_input_buffer / test_cursor /
#         test_parser / test_read_stdin_once / test_new_input
# 去重原则：保留门面级公开行为测试；独有断言迁移至此；重复类删除。
# ═══════════════════════════════════════════════════════════

class TestKeyEventReprRegression:
    """KeyEvent repr 输出包含 kind（合并自 test_input_unified）。"""

    def test_repr_contains_kind_regression(self):
        ev = KeyEvent(kind="enter", char="\r", raw=b"\r")
        assert "enter" in repr(ev)


class TestProcessEventsRegression:
    """process_events() 委托 read_stdin_once()（合并自 test_input_unified）。"""

    @pytest.fixture
    def pipe_input(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_process_events_no_data_regression(self, pipe_input):
        """空队列（无 stdin 数据）时 process_events 不抛异常。"""
        pipe_input.process_events()

    def test_process_events_with_pipe_regression(self, tmp_path):
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

    def test_process_events_with_enter_regression(self, tmp_path):
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

    def test_process_events_multiple_bytes_regression(self, tmp_path):
        """process_events 一次处理多个输入字节（通过粘贴检测批量读取）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            os.write(w_fd, b"hello")
            inp.process_events()
            assert inp.get_current_text() == "hello"
        finally:
            os.close(w_fd)
            os.close(r_fd)


class TestParseEscapeSequenceRegression:
    """parse_sequence() I/O 测试（合并自 test_parser，os.pipe 模拟）。"""

    @pytest.fixture
    def parse_input(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_arrow_up_regression(self, parse_input):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[A")
            assert parse_input.parse_sequence(r_fd).kind == "arrow_up"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_arrow_down_regression(self, parse_input):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[B")
            assert parse_input.parse_sequence(r_fd).kind == "arrow_down"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_home_regression(self, parse_input):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[H")
            assert parse_input.parse_sequence(r_fd).kind == "home"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_end_regression(self, parse_input):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[F")
            assert parse_input.parse_sequence(r_fd).kind == "end"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_delete_tilde_regression(self, parse_input):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[3~")
            assert parse_input.parse_sequence(r_fd).kind == "delete"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_csi_u_shift_enter_regression(self, parse_input):
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[13;2u")
            ev = parse_input.parse_sequence(r_fd)
            assert ev.kind == "char"
            assert ev.char == "\n"
            assert ev.modifier == 2
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_ss3_f1_regression(self, parse_input):
        """SS3 序列（ESC O P = F1）→ f1（方向A 步骤1：不再 unknown）。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"OP")
            assert parse_input.parse_sequence(r_fd).kind == "f1"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_alt_backspace_regression(self, parse_input):
        """ESC DEL → backspace with modifier=1。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x7f")
            ev = parse_input.parse_sequence(r_fd)
            assert ev.kind == "backspace"
            assert ev.modifier == 1
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_double_esc_regression(self, parse_input):
        """双 ESC → interrupt。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"\x1b")
            assert parse_input.parse_sequence(r_fd).kind == "interrupt"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_single_esc_timeout_regression(self, parse_input):
        """单 ESC（无后续字节）→ escape。"""
        r_fd, w_fd = os.pipe()
        try:
            assert parse_input.parse_sequence(r_fd).kind == "escape"
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_other_esc_combination_regression(self, parse_input):
        """其他 ESC 组合（ESC+可打印）→ alt_char（方向A 步骤1：不再 interrupt）。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"x")
            ev = parse_input.parse_sequence(r_fd)
            assert ev.kind == "alt_char"
            assert ev.char == "x"
            assert ev.modifier == 3
        finally:
            os.close(r_fd)
            os.close(w_fd)

    def test_ctrl_arrow_left_regression(self, parse_input):
        """Ctrl+左箭头 → arrow_left with modifier=5。"""
        r_fd, w_fd = os.pipe()
        try:
            os.write(w_fd, b"[1;5D")
            ev = parse_input.parse_sequence(r_fd)
            assert ev.kind == "arrow_left"
            assert ev.modifier == 5
        finally:
            os.close(r_fd)
            os.close(w_fd)


class TestParseEscapeSequenceMockRegression:
    """parse_sequence 的 mock 测试（合并自 test_parser，隔离真实 fd I/O）。"""

    def test_select_error_returns_escape_regression(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "test_history")
            with patch("select.select", side_effect=ValueError):
                ev = inp.parse_sequence(0)
                assert ev.kind == "escape"
        finally:
            os.close(fd)

    def test_os_read_empty_returns_escape_regression(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "test_history")
            with patch("select.select", return_value=([0], [], [])):
                with patch("os.read", return_value=b""):
                    ev = inp.parse_sequence(0)
                    assert ev.kind == "escape"
        finally:
            os.close(fd)

    def test_os_read_error_returns_escape_regression(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "test_history")
            with patch("select.select", return_value=([0], [], [])):
                with patch("os.read", side_effect=OSError):
                    ev = inp.parse_sequence(0)
                    assert ev.kind == "escape"
        finally:
            os.close(fd)


class TestReadStdinOnceRegression:
    """read_stdin_once() 正常路径测试（合并自 test_read_stdin_once，os.pipe 模拟）。"""

    @staticmethod
    def _create_input(pipe_fd, tmp_path) -> Input:
        return Input(fd=pipe_fd, history_file=tmp_path / "test_history")

    def test_read_stdin_once_no_data_regression(self, tmp_path) -> None:
        """无数据时返回 False，不阻塞。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            assert inp.read_stdin_once() is False
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_char_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """写入字符 'a' 后正确分发到缓冲区。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            os.write(w_fd, b"a")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "a"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_enter_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """Enter 键写入后 has_queued_input() 返回 True。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            inp.handle_chars("test")
            os.write(w_fd, b"\r")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp.has_queued_input()
            assert inp.get_queued_input() == "test"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_interrupt_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """Ctrl+C 触发 _do_interrupt()，缓冲区被清空并设置 interrupted。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            inp.handle_chars("hello")
            os.write(w_fd, b"\x03")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == ""
            assert inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_paused_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """暂停状态下不读取数据，返回 False。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            inp.pause_io()
            os.write(w_fd, b"a")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is False
            assert inp.get_current_text() == ""
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_stopped_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """已停止状态下不读取数据，返回 False。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            inp.stop_io()
            os.write(w_fd, b"a")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is False
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_paste_detection_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """粘贴检测：快速连续写入多个字符应被识别为粘贴。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            os.write(w_fd, b"hello")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "hello"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_backspace_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """退格键正确删除字符。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            inp.handle_chars("abc")
            os.write(w_fd, b"\x7f")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "ab"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_tab_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """Tab 键插入制表符。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            os.write(w_fd, b"\t")
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "\t"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_eof_no_crash_regression(self, tmp_path, wait_pipe_readable_fixture) -> None:
        """pipe 写入端关闭后不崩溃，返回 False。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = self._create_input(r_fd, tmp_path)
            os.close(w_fd)
            assert wait_pipe_readable_fixture(r_fd)
            assert inp.read_stdin_once() is False
        finally:
            os.close(r_fd)


class TestFlushStdinBufferRegression:
    """Input.flush_stdin_buffer() 公开方法测试（合并自 test_input_unified）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_flush_stdin_buffer_no_data_regression(self, inp):
        """无数据时 flush_stdin_buffer() 快速返回，不抛异常。"""
        inp.flush_stdin_buffer()

    def test_flush_stdin_buffer_after_call_flags_unchanged_regression(self, inp):
        """flush_stdin_buffer() 调用后标志位正常（不改变 I/O 状态）。"""
        inp.start_io()
        io_was_running = inp.is_io_running
        active_was_set = inp._active.is_set()
        stop_was_set = inp._stop.is_set()

        inp.flush_stdin_buffer()

        assert inp.is_io_running == io_was_running
        assert inp._active.is_set() == active_was_set
        assert inp._stop.is_set() == stop_was_set
        inp.stop_io()

    def test_flush_stdin_buffer_respects_max_flush_regression(self, inp):
        """max_flush 参数限制生效：传递给 _flush_stdin_residual。"""
        call_count = [0]

        def fake_select(rlist, wlist, xlist, timeout):
            call_count[0] += 1
            return ([inp._fd], [], [])

        with patch("src.tui._input.select.select", side_effect=fake_select):
            inp.flush_stdin_buffer(max_flush=3)
            assert call_count[0] <= 3 + 1  # +1 容差（tcflush 路径可能额外调用）


class TestInputReadMethodsRegression:
    """read_utf8_char / try_read_paste 测试（合并自 test_new_input）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(
                fd=fd,
                history_file=tmp_path / "test_history",
                term_width_cache=MagicMock(),
            )
        finally:
            os.close(fd)

    def test_try_read_paste_single_char_regression(self, inp):
        """try_read_paste: 无后续数据时返回原字符。"""
        with patch("select.select", return_value=([], [], [])):
            assert inp.try_read_paste(0, "a") == "a"

    def test_read_utf8_char_valid_2byte_regression(self, inp):
        """read_utf8_char: 有效 2 字节 UTF-8 序列正确解码。"""
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", return_value=b"\xa9"):
                assert inp.read_utf8_char(0, 0xC3) == "é"

    def test_read_utf8_char_invalid_first_byte_regression(self, inp):
        """read_utf8_char: 无效首字节返回 None。"""
        assert inp.read_utf8_char(0, 0x80) is None


class TestUnescapeRegression:
    """_unescape 静态方法测试（合并自 test_input_unified/test_input_buffer）。"""

    def test_no_escape_regression(self):
        assert Input._unescape("hello") == "hello"

    def test_escaped_newline_regression(self):
        assert Input._unescape(r"hello\nworld") == "hello\nworld"

    def test_multiple_newlines_regression(self):
        assert Input._unescape(r"a\nb\nc") == "a\nb\nc"

    def test_empty_regression(self):
        assert Input._unescape("") == ""


class TestLoadHistoryRegression:
    """load_history 去重 + 合并测试（合并自 test_input_buffer）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_load_history_empty_file_regression(self, inp):
        """空历史文件不报错，保留空历史。"""
        with patch("src.tui._input._read_history_file", return_value=("", False)):
            inp.load_history()
        assert inp._history == []

    def test_load_history_from_mock_file_regression(self, inp):
        """load_history 从 mock 文件加载，正确反转顺序。"""
        with patch("src.tui._input._read_history_file",
                   return_value=("line1\nline2\nline3\n", True)):
            with patch("src.tui._input._compact_history_file"):
                inp.load_history()
        assert inp._history[0] == "line3"
        assert len(inp._history) == 3

    def test_load_history_merge_existing_regression(self, inp):
        """load_history 合并到已有内存历史。"""
        inp._history = ["mem_entry"]
        with patch("src.tui._input._read_history_file",
                   return_value=("file_entry\n", True)):
            with patch("src.tui._input._compact_history_file"):
                inp.load_history()
        assert "mem_entry" in inp._history
        assert "file_entry" in inp._history


class TestBufferExtraRegression:
    """缓冲操作独有断言（合并自 test_input_buffer）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_handle_char_filter_control_regression(self, inp):
        """handle_char 过滤不可打印控制字符。"""
        received = []

        def cb(text, pos):
            received.append((text, pos))

        inp.set_echo_callback(cb)
        inp.handle_char('x')
        received.clear()
        inp.handle_char('\x03')  # Ctrl+C 应被静默忽略
        assert received == []
        assert inp.get_current_text() == "x"

    def test_handle_chars_batch_echo_once_regression(self, inp):
        """handle_chars 批量插入（粘贴场景）只触发一次回显。"""
        received = []

        def cb(text, pos):
            received.append((text, pos))

        inp.set_echo_callback(cb)
        inp.handle_chars("hello world")
        assert len(received) == 1
        assert received[0] == ("hello world", 11)

    def test_handle_chars_multiline_paste_regression(self, inp):
        """handle_chars 粘贴含换行的文本。"""
        inp.handle_chars("line1\nline2")
        assert inp.get_current_text() == "line1\nline2"

    def test_enter_adds_to_history_regression(self, inp):
        """Enter 添加输入到历史（mock 磁盘写入以隔离真实历史文件）。

        _enter → _append_history_locked 真实执行历史入内存；
        仅 mock 模块级 _append_to_history_file 隔离磁盘写入，避免自证断言。
        """
        inp.handle_chars("test line")
        with patch("src.tui._input._append_to_history_file", return_value=True):
            inp._enter()
        assert "test line" in inp._history

    def test_set_buffer_clears_submitted_regression(self, inp):
        """set_buffer 清除残留的提交状态。"""
        inp.handle_chars("old")
        with patch.object(inp, "_append_history_locked", return_value=None):
            inp._enter()  # 提交
        inp.set_buffer("new")
        assert inp.get_queued_input() is None  # 残留被清除

    def test_drain_all_resets_history_regression(self, inp):
        """drain_all 重置历史导航状态。"""
        inp._history = ["old"]
        inp._history_idx = 0
        inp._saved_input_before_history = "original"
        inp.drain_all()
        assert inp._history_idx == -1
        assert inp._saved_input_before_history == ""


class TestDrainAllClearsInputReady:
    """BUG-T7 — drain_all 清理 _input_ready 事件（不残留 set 状态）。"""

    def test_drain_all_clears_input_ready_regression(self, tmp_path):
        """_enter 后 has_queued_input() True，drain_all() 后 False 且 wait 超时 False。"""
        from src.tui._input import Input

        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            inp.handle_chars("drain_test")
            inp._enter()
            assert inp.has_queued_input() is True
            submitted, buffer_text = inp.drain_all()
            assert submitted == "drain_test"
            assert inp.get_current_text() == ""
            # 事件残留清除：不再 set
            assert inp.has_queued_input() is False
            assert inp.wait_until_ready(timeout=0) is False
        finally:
            os.close(fd)

    def test_drain_all_clears_event_without_submitted_regression(self, tmp_path):
        """无排队输入时 drain_all 亦清除事件（防止旧残留 set 状态）。"""
        from src.tui._input import Input

        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            inp._input_ready.set()  # 模拟残留 set 状态（_submitted_text 为空串）
            submitted, _ = inp.drain_all()
            assert submitted == ""  # 事件已 set 但无提交文本 → 空串
            assert inp.has_queued_input() is False
            assert inp.wait_until_ready(timeout=0) is False
        finally:
            os.close(fd)


class TestEditingExtraRegression:
    """编辑操作独有断言（合并自 test_input_buffer）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_backspace_exits_history_regression(self, inp):
        """历史导航模式下退格退出导航。"""
        inp._history = ["old1", "old2"]
        inp._history_idx = 0
        inp._buffer = "old1"
        inp._cursor_pos = 4
        inp._backspace()
        assert inp._history_idx == -1
        assert inp.get_current_text() == "old"

    def test_delete_at_end_regression(self, inp):
        """光标在末尾时 Del 无操作。"""
        inp.handle_chars("abc")
        inp._delete()
        assert inp.get_current_text() == "abc"

    def test_home_multiline_regression(self, inp):
        """多行文本：Home 跳到当前逻辑行首。"""
        inp.handle_chars("line1\nline2")
        inp._home()
        received = []

        def cb(text, pos):
            received.append((text, pos))

        inp.set_echo_callback(cb)
        inp.handle_char('>')
        assert "line1\n>line2" in inp.get_current_text()

    def test_word_left_basic_regression(self, inp):
        """_word_left 词边界移动（插入点验证）。"""
        inp.handle_chars("hello world")
        inp._word_left()
        received = []

        def cb(text, pos):
            received.append((text, pos))

        inp.set_echo_callback(cb)
        inp.handle_char('x')
        assert inp.get_current_text().startswith("hello xworld")

    def test_word_right_basic_regression(self, inp):
        """_word_right 词边界移动（插入点验证）。"""
        inp.handle_chars("hello world")
        for _ in range(11):
            inp._left()
        inp._word_right()
        received = []

        def cb(text, pos):
            received.append((text, pos))

        inp.set_echo_callback(cb)
        inp.handle_char('>')
        assert "hello >world" in inp.get_current_text()


class TestHistoryExtraRegression:
    """历史导航独有断言（合并自 test_input_buffer）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_up_then_edit_exits_history_regression(self, inp):
        """历史浏览中编辑退出导航。"""
        inp._history = ["stored"]
        inp._up()  # 进入历史导航
        assert inp._history_idx == 0
        inp.handle_char('!')  # 编辑
        assert inp._history_idx == -1

    def test_down_no_navigation_regression(self, inp):
        """非导航模式下 _down 无操作。"""
        inp.handle_chars("text")
        inp._down()
        assert inp.get_current_text() == "text"

    def test_history_indicator_navigation_mode_regression(self, inp):
        """历史导航模式下指示器显示 位置/总数。"""
        inp._history = ["a", "b", "c"]
        inp._history_idx = 1
        assert "历史 2/3" in inp.get_history_indicator()

    def test_echo_with_history_indicator_regression(self, inp):
        """历史导航模式下回显包含指示器，光标位置仍为原始文本长度。"""
        inp._history = ["hist1", "hist2"]
        inp._history_idx = 0
        inp._buffer = "hist1"
        inp._cursor_pos = 5
        calls = []

        def cb(text, pos):
            calls.append((text, pos))

        inp.set_echo_callback(cb)
        inp._echo("hist1")
        assert "历史 1/2" in calls[0][0]
        assert calls[0][1] == 5


class TestCallbacksExtraRegression:
    """回调接口独有断言（合并自 test_input_unified）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_completion_navigate_regression(self, inp):
        inp.set_completion_navigate_callback(lambda d, t: t + "_nav")
        inp.handle_chars("prefix")
        inp._handle_arrow_up()
        assert inp.get_current_text() == "prefix_nav"

    def test_auto_completion_regression(self, inp):
        results = []
        inp.set_auto_completion_callback(lambda t: results.append(t))
        inp.handle_char('a')
        inp._trigger_auto_completion()
        assert len(results) == 1
        assert results[0] == "a"

    def test_dismiss_completion_regression(self, inp):
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp._dismiss_completion()
        assert len(dismissed) == 1

    def test_special_key_does_not_toggle_active_regression(self, inp):
        """_handle_special_key 直接调用回调，不操作 _active 标志。"""
        results = []
        inp.set_special_key_callback(lambda a, t: results.append((a, t)) or t)
        active_before = inp._active.is_set()
        inp._handle_special_key('vim')
        active_after = inp._active.is_set()
        assert len(results) == 1
        assert results[0][0] == 'vim'
        assert active_before == active_after

    def test_capture_bytes_and_drain_regression(self, inp):
        inp.capture_bytes(b"\xff\xfe")
        assert inp.drain_captured() != ""
        assert inp.drain_captured() == ""  # 已清空

    def test_get_history_indicator_empty_regression(self, inp):
        assert inp.get_history_indicator() == ""


class TestParsingExtraRegression:
    """解析独有断言（合并自 test_input_unified/test_parser）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_unknown_control_regression(self, inp):
        """NUL 字节 → unknown。"""
        ev = inp.feed_byte(0x00)
        assert ev is not None
        assert ev.kind == "unknown"

    def test_csi_u_shift_enter_regression(self):
        """CSI u shift+Enter → char '\\n' modifier 2。"""
        ev = Input._dispatch_csi([13, 2], 'u')
        assert ev.kind == "char"
        assert ev.char == "\n"
        assert ev.modifier == 2

    def test_csi_u_alt_enter_regression(self):
        """CSI u alt+Enter → char '\\n' modifier 3。"""
        ev = Input._dispatch_csi([13, 3], 'u')
        assert ev.kind == "char"
        assert ev.char == "\n"
        assert ev.modifier == 3

    def test_csi_unknown_terminator_regression(self):
        """未知 CSI 终结符 → unknown。"""
        # 'Z' 已映射为 Shift+Tab（Claude TUI parity 步骤 1.4），改用 'q' 作为未知样本
        ev = Input._dispatch_csi([], 'q')
        assert ev.kind == "unknown"


class TestComputeCursorPreciseRegression:
    """compute_cursor() 精确断言（合并自 test_cursor）。"""

    @pytest.fixture
    def mock_width_cache(self):
        """Mock TerminalWidthCache 返回固定宽度 80、高度 24。"""
        cache = MagicMock()
        cache.get_width.return_value = 80
        cache.get_height.return_value = 24
        return cache

    @pytest.fixture
    def inp(self, mock_width_cache, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(
                fd=fd,
                history_file=tmp_path / "test_history",
                term_width_cache=mock_width_cache,
            )
        finally:
            os.close(fd)

    def test_single_line_cursor_at_end_regression(self, mock_width_cache, inp):
        inp_inst = inp
        r_cursor, cursor_col, vis_row, vis_col = inp_inst.compute_cursor(
            text="hello world", cursor_pos=11,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        assert vis_row == 0
        assert r_cursor == 22
        assert cursor_col == 14

    def test_empty_text_default_position_regression(self, mock_width_cache, inp):
        r_cursor, cursor_col, vis_row, vis_col = inp.compute_cursor(
            text="", cursor_pos=0,
            bottom_lines=4, subagent_lines=0, completion_height=0,
        )
        assert vis_row == 0
        assert vis_col == 0
        assert r_cursor == 24

    def test_multiline_text_cursor_in_middle_regression(self, mock_width_cache, inp):
        text = "line one\nline two\nline three"
        r_cursor, cursor_col, vis_row, vis_col = inp.compute_cursor(
            text=text, cursor_pos=14,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        assert vis_row == 1
        assert vis_col == 5
        assert r_cursor == 23

    def test_tab_expansion_vis_col_regression(self, mock_width_cache, inp):
        r_cursor, cursor_col, vis_row, vis_col = inp.compute_cursor(
            text="a\tb", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        assert vis_col == 4

    def test_completion_height_offset_regression(self, mock_width_cache, inp):
        mock_width_cache.get_height.return_value = 50
        r1, _, _, _ = inp.compute_cursor(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        r2, _, _, _ = inp.compute_cursor(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=3,
        )
        assert r2 > r1

    def test_subagent_lines_offset_regression(self, mock_width_cache, inp):
        r1, _, _, _ = inp.compute_cursor(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        r2, _, _, _ = inp.compute_cursor(
            text="hi", cursor_pos=2,
            bottom_lines=6, subagent_lines=2, completion_height=0,
        )
        assert r2 == r1 + 2

    def test_clamp_to_terminal_height_regression(self, mock_width_cache, inp):
        mock_width_cache.get_height.return_value = 5
        r_cursor, _, _, _ = inp.compute_cursor(
            text="line 1\nline 2\nline 3\nline 4", cursor_pos=25,
            bottom_lines=3, subagent_lines=0, completion_height=0,
        )
        assert 1 <= r_cursor <= 5

    def test_cursor_col_clamp_to_width_regression(self, mock_width_cache, inp):
        mock_width_cache.get_width.return_value = 20
        _, cursor_col, _, _ = inp.compute_cursor(
            text="a very long line that exceeds terminal width", cursor_pos=50,
            bottom_lines=6, subagent_lines=0, completion_height=0,
        )
        assert 1 <= cursor_col <= 20


# ═══════════════════════════════════════════════════════════
# 方向A 步骤1/2 拆分回归测试（新增，2026-07-31）
# ═══════════════════════════════════════════════════════════

class TestInputFacade:
    """薄外观委托测试（方向A 步骤1）：Input 保留全部公开 API，方法体委托三组件。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭，修复 os.open /dev/null 泄漏）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_components_created(self, inp):
        """Input 组合持有 InputIO / InputBufferEditor / InputDispatcher。"""
        from src.tui._input_io import InputIO
        from src.tui._input_buffer import InputBufferEditor
        from src.tui._input_dispatcher import InputDispatcher
        assert isinstance(inp._io, InputIO)
        assert isinstance(inp._buffer_editor, InputBufferEditor)
        assert isinstance(inp._dispatcher, InputDispatcher)

    def test_facade_buffer_delegation(self, inp):
        """缓冲操作经外观委托 InputBufferEditor，行为一致。"""
        inp.handle_chars("facade test")
        assert inp.get_current_text() == "facade test"
        assert inp._buffer_editor.get_current_text() == "facade test"
        inp.set_buffer("prefill")
        assert inp.get_current_text() == "prefill"
        inp.reset()
        assert inp.get_current_text() == ""
        assert inp._buffer_editor.get_current_text() == ""

    def test_facade_queue_delegation(self, inp):
        """队列语义经外观委托：_enter → get_queued_input。"""
        inp.handle_chars("queued")
        inp._enter()
        assert inp.has_queued_input()
        assert inp._buffer_editor.has_queued_input()
        assert inp.get_queued_input() == "queued"
        assert inp._buffer_editor.get_queued_input() is None

    def test_facade_io_delegation(self, inp):
        """I/O 状态经外观委托 InputIO。"""
        inp.start_io()
        assert inp.is_io_running
        assert inp._io.is_io_running
        inp.pause_io()
        assert not inp._io.active.is_set()
        inp.resume_io()
        assert inp._io.active.is_set()
        inp.stop_io()
        assert not inp.is_io_running

    def test_facade_history_delegation(self, inp):
        """历史管理经外观委托 InputBufferEditor。"""
        inp._history = ["a", "b"]
        inp._history_idx = 0
        assert inp._buffer_editor._history == ["a", "b"]
        assert inp._buffer_editor._history_idx == 0
        assert inp.get_history_indicator() == " [历史 1/2]"

    def test_facade_callback_delegation(self, inp):
        """回调设置经外观委托到对应组件。"""
        special = lambda a, t: t
        comp = lambda t: t
        dismiss = lambda: None
        nav = lambda d, t: t
        auto = lambda t: None
        interrupt = lambda: None
        inp.set_special_key_callback(special)
        inp.set_completion_callback(comp)
        inp.set_dismiss_completion_callback(dismiss)
        inp.set_completion_navigate_callback(nav)
        inp.set_auto_completion_callback(auto)
        inp.set_interrupt_callback(interrupt)
        assert inp._dispatcher._special_key_callback is special
        assert inp._dispatcher._completion_callback is comp
        assert inp._dispatcher._dismiss_completion_callback is dismiss
        assert inp._dispatcher._completion_navigate_callback is nav
        assert inp._dispatcher._auto_completion_callback is auto
        assert inp._dispatcher._interrupt_callback is interrupt

        echo = lambda text, pos: None
        inp.set_echo_callback(echo)
        assert inp._buffer_editor._echo_callback is echo

    def test_facade_suppress_enter_delegation(self, inp):
        """_suppress_enter 读写经外观委托 InputDispatcher。"""
        inp.set_suppress_enter(True)
        assert inp.get_suppress_enter() is True
        assert inp._dispatcher.get_suppress_enter() is True
        inp.set_suppress_enter(False)
        assert inp.get_suppress_enter() is False

    def test_facade_capture_delegation(self, inp):
        """捕获缓冲区经外观委托 InputDispatcher。"""
        inp.capture_bytes(b"\x01")
        assert inp._dispatcher.drain_captured() != ""
        assert inp.drain_captured() == ""

    def test_facade_read_stdin_once_delegation(self, tmp_path):
        """read_stdin_once / process_events 经外观委托 InputDispatcher。"""
        import select as _sel
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            os.write(w_fd, b"a")
            ready, _, _ = _sel.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "a"
        finally:
            os.close(w_fd)
            os.close(r_fd)


class TestInputIOUnit:
    """InputIO 单元测试（方向A 步骤1）：读取原语 + I/O 状态机。"""

    @pytest.fixture
    def io(self):
        """创建 InputIO 实例（P2-7：fixture 确保 fd 关闭）。"""
        from src.tui._input_io import InputIO
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield InputIO(fd=fd)
        finally:
            os.close(fd)

    def test_read_utf8_char_timeout_regression(self, io):
        """read_utf8_char 超时（后续字节未到）返回 None（截断序列）。"""
        with patch("select.select", return_value=([], [], [])):
            assert io.read_utf8_char(0, 0xC3) is None

    def test_read_utf8_char_truncated_regression(self, io):
        """read_utf8_char 只读到部分字节时返回 None。"""
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", side_effect=[b"\xbd", b""]):
                # 3 字节序列：首字节 0xE4 已给，续字节只读到 1 个 → 解码失败 → None
                assert io.read_utf8_char(0, 0xE4) is None

    def test_try_read_paste_backoff_regression(self, io):
        """try_read_paste 退避：无后续数据时计数器递增并返回原字符。"""
        with patch("select.select", return_value=([], [], [])):
            assert io.try_read_paste(0, "a") == "a"
            assert io._paste_skip_counter == 1

    def test_try_read_paste_paste_detection_regression(self, io):
        """try_read_paste 检测到粘贴并读取 extra（262144 上限不变）。"""
        def fake_select(rlist, wlist, xlist, timeout):
            return ([0], [], [])
        with patch("select.select", side_effect=fake_select):
            with patch("os.read", side_effect=[b"bc", b""]):
                assert io.try_read_paste(0, "a") == "abc"

    def test_flush_stdin_buffer_mock_tcflush_regression(self, io):
        """flush_stdin_buffer 在 HAS_TERMIOS 时调用 tcflush（mock）。"""
        with patch("src.tui._input_io.HAS_TERMIOS", True):
            with patch("src.tui._input_io.termios") as mock_termios:
                mock_termios.TCIFLUSH = 0
                io.flush_stdin_buffer(max_flush=1)
                mock_termios.tcflush.assert_called()

    def test_start_stop_io_state_regression(self, io):
        """start_io/stop_io 状态机：_fd_status / _io_started / _exit_reason。"""
        io.start_io()
        assert io.is_io_running
        assert io.fd_status == "ok"
        io.record_select_error()
        assert io.select_error_count == 1
        io.stop_io()
        assert not io.is_io_running
        assert io.fd_status == "ok"

    def test_slow_multibyte_no_char_loss_regression(self, io):
        """方向2 — 慢速 3 字节中文：首次 select 超时不丢首字节，二次补齐后正确解码。

        修复前 read_utf8_char 续字节超时 → 解码失败返回 None → 首字节被
        capture（慢速多字节丢字节）。
        """
        # 首次调用：续字节 select 超时（无数据）→ 返回 None，首字节存入 _utf8_partial
        with patch("select.select", return_value=([], [], [])):
            assert io.read_utf8_char(0, 0xE4) is None
        assert io._utf8_partial == b"\xe4", (
            f"首字节应保留在 _utf8_partial，实际 {io._utf8_partial!r}"
        )
        # 二次调用：续字节到达（0xB8 为第二个续字节）→ 拼接补齐（E4 B8 AD = "中"）
        # 首字节 E4（3 字节），partial 已有 1 字节 + 当前 1 字节 → 还需读 1 个续字节
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", return_value=b"\xad"):
                assert io.read_utf8_char(0, 0xB8) == "中"
        assert io._utf8_partial == b"", "补齐后 partial 应清空"

    def test_eof_fd_status_error_regression(self, io):
        """方向2 — record_eof 达阈值置 _fd_status="error"（can_read 停止读取）。

        修复前达阈值仅置 _exit_reason，_fd_status 保持 "ok" → can_read() 恒
        True → render 线程每帧 select+read 空转 + 日志刷屏。
        """
        from src.api.escape_monitor._history import _EOF_THRESHOLD
        for _ in range(_EOF_THRESHOLD):
            io.record_eof()
        assert io.fd_status == "error"
        assert io.exit_reason == "eof"
        assert io.can_read() is False  # 不再空转读取


# ═══════════════════════════════════════════════════════════
# 方向1 B8 — _flush_stdin_residual 总体时间预算（最坏 2.5s → <0.2s）
# ═══════════════════════════════════════════════════════════

class TestFlushResidualBudget:
    """方向1 B8 — _flush_stdin_residual 时间预算回归。

    旧实现每次 select 超时固定 0.05s——fd 恒可读（持续输入）时 50 字节 × 0.05s
    最坏阻塞 2.5s（render 线程卡顿）。修复后总体预算（默认 50ms）+ 短超时
    （≤1ms）非阻塞排空：超预算即 break。本测试 mock select 模拟真实阻塞耗时，
    验证总耗时受预算兜底（< 0.2s）且未排完 max_flush 即 break。
    """

    @pytest.fixture
    def io(self):
        """创建 InputIO 实例（P2-7：fixture 确保 fd 关闭）。"""
        from src.tui._input_io import InputIO
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield InputIO(fd=fd)
        finally:
            os.close(fd)

    def test_flush_residual_budget_limits_worst_case(self, io):
        """mock select 恒 ready + 模拟真实阻塞耗时 → 总耗时 < 0.2s（预算兜底）。"""
        import time as _time

        def fake_select(rlist, wlist, xlist, timeout):
            # 模拟真实 select：恒可读但仍按 timeout 阻塞（旧实现 0.05s/次
            # → 50 次 2.5s；新实现每次 ≤0.001s → 预算 50ms 内 break）
            if timeout > 0:
                _time.sleep(timeout)
            return (rlist, [], [])

        reads = []
        with patch("select.select", side_effect=fake_select):
            with patch("os.read", side_effect=lambda fd, n: reads.append(n) or b"x"):
                start = _time.monotonic()
                io._flush_stdin_residual(max_flush=1000)  # 不设限 → 预算兜底
                elapsed = _time.monotonic() - start
        assert elapsed < 0.2
        assert len(reads) < 1000  # 预算触发 break（未排完 max_flush）

    def test_flush_residual_no_data_fast(self, io):
        """无数据时 select 立即空 → 快速返回（不消耗预算）。"""
        import time as _time
        with patch("select.select", return_value=([], [], [])):
            start = _time.monotonic()
            io._flush_stdin_residual(max_flush=50)
            elapsed = _time.monotonic() - start
        assert elapsed < 0.2

    def test_flush_residual_respects_max_flush(self, io):
        """max_flush 仍生效：恒可读时最多排空 max_flush 字节。"""
        reads = []
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", side_effect=lambda fd, n: reads.append(n) or b"x"):
                io._flush_stdin_residual(max_flush=3)
        assert len(reads) == 3


# ═══════════════════════════════════════════════════════════
# 方向1 步骤1 — 粘贴多字节解码辅助（_decode_paste_bytes / _paste_partial）
# ═══════════════════════════════════════════════════════════

class TestPastePartialDecode:
    """InputIO._decode_paste_bytes 粘贴多字节解码（方向1 B4 前置封装）。

    覆盖：完整多字节一次返回；截断 1-3 字节保留、下次补齐后无 U+FFFD；
    连续多次截断累计正确；中部损坏字节 replace 兜底。
    """

    @pytest.fixture
    def io(self):
        """创建 InputIO 实例（P2-7：fixture 确保 fd 关闭）。"""
        from src.tui._input_io import InputIO
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield InputIO(fd=fd)
        finally:
            os.close(fd)

    def test_complete_multibyte_returns_immediately(self, io):
        """完整多字节序列一次返回（无 U+FFFD）。"""
        # "你" = E4 BD A0
        assert io._decode_paste_bytes(b"\xe4\xbd\xa0") == "你"
        assert io._paste_partial == b""

    def test_complete_ascii_returns_immediately(self, io):
        """完整 ASCII 一次返回。"""
        assert io._decode_paste_bytes(b"hello") == "hello"
        assert io._paste_partial == b""

    def test_truncated_one_byte_kept(self, io):
        """截断 1 字节（UTF-8 3 字节序列尾 1 字节）保留，补齐后完整。"""
        # "你" 拆为 [E4 BD] + [A0]
        assert io._decode_paste_bytes(b"\xe4\xbd") == ""  # 不产生 U+FFFD
        assert io._paste_partial == b"\xe4\xbd"
        assert io._decode_paste_bytes(b"\xa0") == "你"
        assert io._paste_partial == b""

    def test_truncated_two_bytes_kept(self, io):
        """截断 2 字节（UTF-8 3 字节序列尾 2 字节）保留，补齐后完整。"""
        assert io._decode_paste_bytes(b"\xe4") == ""
        assert io._paste_partial == b"\xe4"
        assert io._decode_paste_bytes(b"\xbd\xa0") == "你"
        assert io._paste_partial == b""

    def test_truncated_three_bytes_kept(self, io):
        """截断 3 字节（UTF-8 4 字节序列尾 3 字节）保留，补齐后完整。"""
        # "𠜎" = F0 A0 9C 8E
        assert io._decode_paste_bytes(b"\xf0") == ""
        assert io._paste_partial == b"\xf0"
        assert io._decode_paste_bytes(b"\xa0\x9c\x8e") == "𠜎"
        assert io._paste_partial == b""

    def test_consecutive_truncations_accumulate(self, io):
        """连续多次截断累计正确。"""
        # "你好" = [E4 BD A0] [E5 A5 BD]；逐字节拆分
        assert io._decode_paste_bytes(b"\xe4") == ""
        assert io._decode_paste_bytes(b"\xbd") == ""
        assert io._paste_partial == b"\xe4\xbd"
        assert io._decode_paste_bytes(b"\xa0") == "你"
        assert io._paste_partial == b""
        assert io._decode_paste_bytes(b"\xe5") == ""
        assert io._decode_paste_bytes(b"\xa5") == ""
        assert io._decode_paste_bytes(b"\xbd") == "好"
        assert io._paste_partial == b""

    def test_invalid_bytes_all_prefixes_fail_replace_fallback(self, io):
        """全部前缀均无法严格解码 → replace 兜底（不崩溃、残留清空）。"""
        # 5 个非法字节：尾部 1-3 字节切割前缀均 decode 失败 → 整体 replace
        result = io._decode_paste_bytes(b"\xff\xff\xff\xff\xff")
        assert result == "\ufffd" * 5
        assert io._paste_partial == b""

    def test_partial_then_ascii_keeps_partial(self, io):
        """残留不完整序列后追加无法补齐的字节 → 整体保留（不产生 U+FFFD）。"""
        assert io._decode_paste_bytes(b"\xe4") == ""
        # \xe4 需要 UTF-8 续字节（0x80-0xBF）；'x' 非续字节 → 整体作为
        # 潜在不完整尾部保留（真实粘贴为合法 UTF-8，此场景为人为构造）。
        result = io._decode_paste_bytes(b"x")
        assert result == ""
        assert io._paste_partial == b"\xe4x"
        # 继续追加续字节：整体仍作为尾部保留（直到可解码才返回）
        result2 = io._decode_paste_bytes(b"\xa0")
        assert result2 == ""
        assert io._paste_partial == b"\xe4x\xa0"


# ═══════════════════════════════════════════════════════════
# 方向1 B4 — 多字节粘贴边界回归（try_read_paste 经 _decode_paste_bytes 解码）
# ═══════════════════════════════════════════════════════════

class TestPasteMultibyteBoundary:
    """方向1 B4 — 粘贴边界无 U+FFFD 污染（多字节截断）。

    覆盖：pipe 多字节字符拆两次写入 → 缓冲完整无 U+FFFD；粘贴 extra 尾部
    截断多字节（旧实现 extra.decode(errors="replace") → U+FFFD）→ 修复后
    无 U+FFFD 且截断字节保留在 _paste_partial（下次补齐）。
    """

    def test_pipe_split_multibyte_char_complete(self, tmp_path):
        """pipe 写入多字节字符被拆两次（先前 2 字节再剩余）→ 缓冲完整无 U+FFFD。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            # "你" = E4 BD A0，拆两次写入（同一 read_stdin_once 前全部落管）
            os.write(w_fd, b"\xe4\xbd")
            os.write(w_fd, b"\xa0")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "你"
            assert "\ufffd" not in inp.get_current_text()
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_paste_extra_truncated_multibyte_no_fffd(self, tmp_path):
        """粘贴 extra 尾部截断多字节（B4 核心）→ 无 U+FFFD、截断字节保留。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            # 粘贴正文：完整"你"（E4 BD A0）+ 下一字符"好"的首字节 E5（截断）。
            # 旧实现 try_read_paste 对 extra=E5 直解 errors="replace" → "你\ufffd"；
            # 修复后 E5 保留 _paste_partial，返回 "你"（无 U+FFFD）。
            os.write(w_fd, b"\xe4\xbd\xa0\xe5")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "你"
            assert "\ufffd" not in inp.get_current_text()
            assert inp._io._paste_partial == b"\xe5"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_try_read_paste_direct_truncated_keeps_partial(self):
        """try_read_paste 直接调用：extra 截断多字节 → 无 U+FFFD、partial 保留。"""
        from src.tui._input_io import InputIO
        r_fd, w_fd = os.pipe()
        try:
            io = InputIO(fd=r_fd)
            # 直接调用场景：first_chars 为已解码的"你"（read_utf8_char 已消费
            # E4 BD A0），管道中仅剩下一字符"好"的首字节 E5（截断）作为粘贴
            # extra——try_read_paste 经 _decode_paste_bytes 保留 E5。
            os.write(w_fd, b"\xe5")
            result = io.try_read_paste(r_fd, "你")
            assert result == "你"
            assert "\ufffd" not in result
            assert io._paste_partial == b"\xe5"
            # 下次补齐剩余 2 字节 → _decode_paste_bytes 完成 "好"
            assert io._decode_paste_bytes(b"\xa5\xbd") == "好"
            assert io._paste_partial == b""
        finally:
            os.close(w_fd)
            os.close(r_fd)


class TestInputBufferEditorUnit:
    """InputBufferEditor 单元测试（方向A 步骤1）：缓冲编辑 + 历史 + _input_ready。"""

    @pytest.fixture
    def editor(self, tmp_path):
        """创建 InputBufferEditor 实例（注入 _HistoryIO 保持 patch 路径）。"""
        from src.tui._input_buffer import InputBufferEditor
        from src.tui._input import _HistoryIO
        return InputBufferEditor(
            history_file=tmp_path / "test_history",
            history_io=_HistoryIO(),
        )

    def test_editor_edit_ops(self, editor):
        """编辑操作：handle_char / _backspace / _left / _right。"""
        editor.handle_chars("abc")
        assert editor.get_current_text() == "abc"
        editor._left()
        editor.handle_char('X')
        assert editor.get_current_text() == "abXc"
        editor._backspace()
        assert editor.get_current_text() == "abc"

    def test_editor_history_dedup(self, editor):
        """历史去重：重复条目前移不重复。"""
        with patch("src.tui._input._append_to_history_file", return_value=True):
            editor._history = ["a", "b"]
            editor._buffer = "b"
            editor._cursor_pos = 1
            editor._enter()
        assert editor._history == ["b", "a"]

    def test_editor_input_ready_event_semantics(self, editor):
        """_input_ready 事件语义：enter set / get_queued_input clear。"""
        assert not editor._input_ready.is_set()
        editor._buffer = "text"
        editor._cursor_pos = 4
        with patch("src.tui._input._append_to_history_file", return_value=True):
            editor._enter()
        assert editor._input_ready.is_set()
        assert editor.get_queued_input() == "text"
        assert not editor._input_ready.is_set()

    def test_editor_wait_until_ready(self, editor):
        """wait_until_ready：事件未设置超时返回 False，设置后返回 True。"""
        assert editor.wait_until_ready(timeout=0.05) is False
        editor._input_ready.set()
        assert editor.wait_until_ready(timeout=0.05) is True


class TestDoInterruptCallbackInjection:
    """interrupt 回调注入（方向A 步骤1）：未注入不抛异常，注入后被调用。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_do_interrupt_no_callback_no_exception_regression(self, inp):
        """未注入 interrupt 回调时 _do_interrupt 不抛异常（None 短路）。"""
        inp.handle_chars("hello")
        inp._do_interrupt()
        assert inp.interrupted  # 中断标志仍被设置

    def test_do_interrupt_injected_callback_called_regression(self, inp):
        """注入 mock 回调后被调用。"""
        cb = MagicMock()
        inp.set_interrupt_callback(cb)
        inp.handle_chars("hello")
        inp._do_interrupt()
        cb.assert_called_once()

    def test_do_interrupt_clears_buffer_when_no_queued_regression(self, inp):
        """无排队输入时 _do_interrupt 清空缓冲区。"""
        inp.handle_chars("hello")
        assert inp.get_current_text() == "hello"
        inp._do_interrupt()
        assert inp.get_current_text() == ""
        assert inp.interrupted

    def test_do_interrupt_has_queued_only_flush_stdin_residual_regression(self, inp):
        """P3-3：has_queued_input()==True 分支 → 仅 _flush_stdin_residual()（不 reset）。

        _do_interrupt 在有排队输入时不清空缓冲区（保持待消费的 submitted 文本），
        仅排空 stdin 残留字节后设置中断标志。
        """
        inp.handle_chars("hello")
        inp._enter()  # 提交 → has_queued_input() == True
        assert inp.has_queued_input() is True
        assert inp.get_current_text() == ""  # _enter 清空 buffer

        with patch.object(inp._io, "_flush_stdin_residual") as mock_flush:
            inp._do_interrupt()

        mock_flush.assert_called_once()
        assert inp.interrupted
        # 排队输入仍保留（未被 reset 消费）
        assert inp.has_queued_input() is True
        assert inp.get_queued_input() == "hello"

    def test_do_interrupt_stop_set_early_return_regression(self, inp):
        """P3-3：stop.is_set() 提前返回 → 不设置中断标志、不调用回调。"""
        cb = MagicMock()
        inp.set_interrupt_callback(cb)
        inp.handle_chars("hello")
        inp._stop.set()  # I/O 已停止

        inp._do_interrupt()

        # 提前返回：中断标志未设置、回调未调用、缓冲区未被 reset
        assert inp.interrupted is False
        cb.assert_not_called()
        assert inp.get_current_text() == "hello"

    def test_read_stdin_once_interrupt_with_callback_regression(self, tmp_path):
        """Ctrl+C 经 read_stdin_once 分发到 _do_interrupt 并调用注入回调。"""
        import select as _sel
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            cb = MagicMock()
            inp.set_interrupt_callback(cb)
            inp.start_io()
            inp.handle_chars("hello")
            os.write(w_fd, b"\x03")
            ready, _, _ = _sel.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.interrupted
            assert inp.get_current_text() == ""
            cb.assert_called_once()
        finally:
            os.close(w_fd)
            os.close(r_fd)


class TestWaitUntilReadyFacade:
    """wait_until_ready（方向A 步骤2）：Input 薄外观暴露，_input_ready 事件语义。"""

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "test_history")
        finally:
            os.close(fd)

    def test_wait_until_ready_timeout_false_regression(self, inp):
        """超时返回 False。"""
        assert inp.wait_until_ready(timeout=0.05) is False

    def test_wait_until_ready_set_returns_true_regression(self, inp):
        """事件已设置立即返回 True。"""
        inp._input_ready.set()
        assert inp.wait_until_ready(timeout=0.05) is True

    def test_wait_until_ready_after_enter_regression(self, inp):
        """_enter 后 wait_until_ready 返回 True，get_queued_input 可取文本。"""
        inp.handle_chars("ready")
        inp._enter()
        assert inp.wait_until_ready(timeout=0.05) is True
        assert inp.get_queued_input() == "ready"

    def test_wait_until_ready_thread_safety_regression(self, inp):
        """多线程 set/wait 无竞态（另一线程 _enter，主线程事件等待立即唤醒）。"""
        import threading

        def submit():
            inp.handle_chars("threaded")
            inp._enter()

        t = threading.Thread(target=submit)
        t.start()
        assert inp.wait_until_ready(timeout=5.0) is True
        assert inp.get_queued_input() == "threaded"
        t.join()


# ═══════════════════════════════════════════════════════════
# input hook router 分发路径（步骤 8 useInput 钩子优先分发）
# ═══════════════════════════════════════════════════════════

class TestInputHookRouterFacade:
    """Input 薄外观 set_input_hook_router 委托 + 钩子优先分发。

    验证：
    1. set_input_hook_router 经外观委托 InputDispatcher
    2. router 消费（返回 True）→ 跳过旧回调路径
    3. router 放行（返回 False）/ 未注入 → 走旧路径（零行为变化）
    4. read_stdin_once 内联分发经 router（char 键消费/放行）
    """

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_set_input_hook_router_delegates_to_dispatcher(self, inp):
        """set_input_hook_router 委托 InputDispatcher。"""
        router = lambda ev: False
        inp.set_input_hook_router(router)
        assert inp._dispatcher._input_hook_router is router

    def test_router_consumes_char_skips_buffer(self, inp):
        """router 消费 char → 不插入缓冲区（跳过旧回调路径）。"""
        inp.set_input_hook_router(lambda ev: True)
        inp._dispatch_key_event(KeyEvent(kind="char", char="X"))
        assert inp.get_current_text() == ""

    def test_router_releases_char_to_buffer(self, inp):
        """router 放行 char → 插入缓冲区（走旧路径）。"""
        inp.set_input_hook_router(lambda ev: False)
        inp._dispatch_key_event(KeyEvent(kind="char", char="X"))
        assert inp.get_current_text() == "X"

    def test_no_router_unchanged(self, inp):
        """未注入 router → 零行为变化。"""
        inp._dispatch_key_event(KeyEvent(kind="char", char="X"))
        assert inp.get_current_text() == "X"

    def test_router_consumes_enter_blocks_submit(self, inp):
        """router 消费 enter → 不提交（无排队输入）。"""
        inp.handle_chars("test")
        inp.set_input_hook_router(lambda ev: True)
        with patch("src.tui._input._append_to_history_file", return_value=True):
            inp._dispatch_key_event(KeyEvent(kind="enter"))
        assert not inp.has_queued_input()

    def test_router_release_enter_commits(self, inp):
        """router 放行 enter → 正常提交。"""
        inp.handle_chars("test")
        inp.set_input_hook_router(lambda ev: False)
        with patch("src.tui._input._append_to_history_file", return_value=True):
            inp._dispatch_key_event(KeyEvent(kind="enter"))
        assert inp.has_queued_input()
        assert inp.get_queued_input() == "test"

    def test_router_exception_isolated(self, inp):
        """router 异常 → 放行（走旧路径，不阻断）。"""
        inp.set_input_hook_router(lambda ev: (_ for _ in ()).throw(ValueError("boom")))
        inp._dispatch_key_event(KeyEvent(kind="char", char="X"))
        assert inp.get_current_text() == "X"

    def test_read_stdin_once_char_consumed(self, tmp_path):
        """read_stdin_once 中 char 键被 router 消费（不插入缓冲区）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.start_io()
            inp.set_input_hook_router(lambda ev: True)
            os.write(w_fd, b"a")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == ""
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_read_stdin_once_char_released(self, tmp_path):
        """read_stdin_once 中 char 键被 router 放行 → 插入缓冲区。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.start_io()
            inp.set_input_hook_router(lambda ev: False)
            os.write(w_fd, b"a")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "a"
        finally:
            os.close(w_fd)
            os.close(r_fd)


# ═══════════════════════════════════════════════════════════
# 方向1 步骤1 — _router_consume 公共辅助（策略收敛单一入口）
# ═══════════════════════════════════════════════════════════

class TestRouterConsume:
    """InputDispatcher._router_consume 公共辅助（router 优先分发统一入口）。

    语义：有 router 且 handler 返回 True 则消费（返回 True）；router 为
    None 或抛异常时返回 False（放行）。供 read_stdin_once 内联路径与
    _dispatch_key_event 复用。
    """

    @pytest.fixture
    def inp(self, tmp_path):
        """创建 Input 实例（P2-7：fixture 确保 fd 关闭）。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_no_router_returns_false(self, inp):
        """无 router 时返回 False（放行）。"""
        from src.tui._input_parser import KeyEvent
        assert inp._dispatcher._router_consume(KeyEvent(kind="char", char="a")) is False

    def test_router_returns_true_consumes(self, inp):
        """router 返回 True → 消费（True）。"""
        from src.tui._input_parser import KeyEvent
        inp.set_input_hook_router(lambda ev: True)
        assert inp._dispatcher._router_consume(KeyEvent(kind="char", char="a")) is True

    def test_router_returns_false_releases(self, inp):
        """router 返回 False → 放行（False）。"""
        from src.tui._input_parser import KeyEvent
        inp.set_input_hook_router(lambda ev: False)
        assert inp._dispatcher._router_consume(KeyEvent(kind="char", char="a")) is False

    def test_router_exception_releases(self, inp):
        """router 抛异常 → 放行（False，不阻断输入）。"""
        from src.tui._input_parser import KeyEvent
        inp.set_input_hook_router(lambda ev: (_ for _ in ()).throw(ValueError("boom")))
        assert inp._dispatcher._router_consume(KeyEvent(kind="char", char="a")) is False

    def test_dispatch_key_event_uses_helper(self, inp):
        """_dispatch_key_event 复用 _router_consume（消费跳过旧路径）。"""
        from src.tui._input_parser import KeyEvent
        inp.set_input_hook_router(lambda ev: True)
        inp._dispatch_key_event(KeyEvent(kind="char", char="X"))
        assert inp.get_current_text() == ""  # 被消费，未插入缓冲

    def test_dispatch_key_event_release_helper(self, inp):
        """_dispatch_key_event 复用 _router_consume（放行走旧路径）。"""
        from src.tui._input_parser import KeyEvent
        inp.set_input_hook_router(lambda ev: False)
        inp._dispatch_key_event(KeyEvent(kind="char", char="X"))
        assert inp.get_current_text() == "X"


# ═══════════════════════════════════════════════════════════
# PERF-6 大粘贴 list 拼接（步骤 6.6）
# ═══════════════════════════════════════════════════════════

class TestHandleCharsLargePaste:
    """PERF-6 — handle_chars 大粘贴 list 拼接（O(n) 单次完成）。"""

    def test_handle_chars_large_paste_regression(self, tmp_path):
        """50k 字符粘贴后 buffer 正确、cursor 正确。"""
        from src.tui._input import Input

        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            text = "a" * 50000
            inp.handle_chars(text)
            assert inp.get_current_text() == text
            assert inp._cursor_pos == 50000
            # 光标在中间插入粘贴
            inp._home()
            suffix = "b" * 100
            inp.handle_chars(suffix)
            assert inp._cursor_pos == 100
            assert inp.get_current_text() == suffix + text
        finally:
            os.close(fd)

    def test_handle_chars_large_paste_middle_cursor_regression(self, tmp_path):
        """光标在中间时大粘贴插入正确。"""
        from src.tui._input import Input

        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            inp.handle_chars("hello world")
            inp._home()
            inp._right()
            inp._right()  # 光标在 "he|llo world"
            paste = "X" * 20000
            inp.handle_chars(paste)
            assert inp.get_current_text().startswith("he" + paste)
            assert inp._cursor_pos == 2 + 20000
        finally:
            os.close(fd)

    def test_handle_chars_large_paste_echo_once_regression(self, tmp_path):
        """大粘贴只触发一次回显（批量语义保持）。"""
        from src.tui._input import Input

        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            received = []
            inp.set_echo_callback(lambda t, p: received.append((t, p)))
            inp.handle_chars("z" * 50000)
            assert len(received) == 1
            assert received[0][1] == 50000
        finally:
            os.close(fd)


# ═══════════════════════════════════════════════════════════
# 方向A 步骤1 输入组合键缺陷修复分发测试（2026-08-01）
# ═══════════════════════════════════════════════════════════

class TestCombinationKeyDispatch:
    """组合键分发：Alt+B/F 词跳转 / Shift+Tab 反向循环 / CSI u Ctrl+字母 / F1-F4 router。

    验证 read_stdin_once → _parse_escape_sequence → _dispatch_key_event
    全链路（os.pipe 模拟真实字节流，含 ESC 前缀）。
    """

    def test_alt_b_word_left_regression(self, tmp_path):
        """ESC+b → alt_char → _word_left（词左跳，不再 interrupt）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("hello world")  # 光标在末尾（pos=11）
            os.write(w_fd, b"\x1bb")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp._cursor_pos == 6  # 跳到 "hello " 后（'w' 前）
            assert not inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_alt_f_word_right_regression(self, tmp_path):
        """ESC+f → alt_char → _word_right（词右跳）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("hello world")
            inp._home()
            os.write(w_fd, b"\x1bf")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp._cursor_pos == 6  # 跳到 "hello " 后（'w' 前）
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_shift_tab_reverse_completion_regression(self, tmp_path):
        """CSI u Shift+Tab → tab/modifier=2 → 补全反向循环（delta=-1）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            calls = []
            inp.set_completion_navigate_callback(lambda d, t: calls.append(d) or t)
            inp.handle_chars("pre")
            os.write(w_fd, b"\x1b[9;2u")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert calls == [-1]
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_shift_tab_no_completion_noop_regression(self, tmp_path):
        """CSI u Shift+Tab 且无补全导航回调 → no-op（不插入制表符）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("pre")
            os.write(w_fd, b"\x1b[9;2u")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "pre"  # 未插入 \t
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_csi_u_ctrl_a_home_regression(self, tmp_path):
        """CSI u Ctrl+A（97;5）→ home（光标移到行首）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("hello")
            inp._left()
            inp._left()  # 光标在 "hel|lo"
            os.write(w_fd, b"\x1b[97;5u")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp._cursor_pos == 0  # home
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_f1_router_consumed_regression(self, tmp_path):
        """F1 经 input router 消费（不再静默丢弃）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            consumed = []
            inp.set_input_hook_router(lambda ev: consumed.append(ev.kind) or True)
            os.write(w_fd, b"\x1bOP")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert consumed == ["f1"]
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_f4_router_release_noop_regression(self, tmp_path):
        """F4 router 放行 → no-op（不崩溃、不产生中断）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_input_hook_router(lambda ev: False)
            inp.handle_chars("abc")
            os.write(w_fd, b"\x1bOS")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "abc"
            assert not inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)


# ═══════════════════════════════════════════════════════════
# 方向D 步骤14 — Ctrl+R 反向历史搜索（配置门控）
# ═══════════════════════════════════════════════════════════

class TestReverseSearchEditor:
    """InputBufferEditor 反向历史搜索方法（search_enter/next/prev/exit/is_search_active）。"""

    @pytest.fixture
    def editor(self, tmp_path):
        from src.tui._input_buffer import InputBufferEditor
        from src.tui._input import _HistoryIO
        return InputBufferEditor(
            history_file=tmp_path / "test_history",
            history_io=_HistoryIO(),
        )

    def test_search_enter_builds_matches_most_recent_first(self, editor):
        """search_enter 建立匹配列表（最近优先），idx 指向最近匹配。"""
        editor._history = ["newest match", "middle", "oldest match"]
        assert editor.search_enter("match") is True
        assert editor.is_search_active() is True
        assert editor._search_query == "match"
        assert editor._search_matches == ["newest match", "oldest match"]
        assert editor._search_idx == 0

    def test_search_enter_empty_query_does_not_activate(self, editor):
        """查询为空不进入搜索。"""
        assert editor.search_enter("") is False
        assert editor.is_search_active() is False

    def test_search_enter_no_match_activates_empty(self, editor):
        """查询非空但无匹配时进入搜索（matches 空、idx=-1）。"""
        editor._history = ["entry one", "entry two"]
        assert editor.search_enter("zzz") is True
        assert editor.is_search_active() is True
        assert editor._search_matches == []
        assert editor._search_idx == -1

    def test_search_next_prev_cycle(self, editor):
        """search_next/prev 循环移动。"""
        editor._history = ["a1", "b2", "a3"]
        editor.search_enter("a")
        assert editor._search_matches == ["a1", "a3"]
        assert editor.search_next() == "a3"
        assert editor.search_next() == "a1"  # 循环
        assert editor.search_prev() == "a3"
        assert editor.search_prev() == "a1"

    def test_search_exit_restores_original_buffer(self, editor):
        """search_exit(apply=False) 恢复进入搜索前的缓冲。"""
        editor.handle_chars("partial")
        editor._history = ["entry one", "entry two"]
        editor.search_enter("entry")
        assert editor.get_current_text() == "partial"  # 搜索期间缓冲不变
        editor.search_exit(apply=False)
        assert editor.is_search_active() is False
        assert editor.get_current_text() == "partial"

    def test_search_exit_apply_replaces_buffer(self, editor):
        """search_exit(apply=True) 用当前匹配替换缓冲。"""
        editor._history = ["first entry", "second entry"]
        editor.handle_chars("abc")
        editor.search_enter("entry")
        assert editor._search_matches == ["first entry", "second entry"]
        editor.search_exit(apply=True)
        assert editor.is_search_active() is False
        assert editor.get_current_text() == "first entry"

    def test_enter_in_search_applies_match_not_submit(self, editor):
        """搜索模式 _enter 应用匹配并退出（不提交排队输入）。"""
        editor._history = ["search target", "other"]
        editor.handle_chars("x")
        editor.search_enter("search")
        editor._enter()
        assert editor.is_search_active() is False
        assert editor.get_current_text() == "search target"
        assert not editor.has_queued_input()

    def test_reset_clears_search_state(self, editor):
        """reset 清理搜索状态。"""
        editor._history = ["match me"]
        editor.handle_chars("x")
        editor.search_enter("match")
        assert editor.is_search_active() is True
        editor.reset()
        assert editor.is_search_active() is False
        assert editor._search_matches == []

    def test_drain_all_clears_search_state(self, editor):
        """drain_all 清理搜索状态。"""
        editor._history = ["match me"]
        editor.handle_chars("x")
        editor.search_enter("match")
        editor.drain_all()
        assert editor.is_search_active() is False


class TestReverseSearchDispatch:
    """方向D 步骤14 — InputDispatcher Ctrl+R 路由（配置门控，默认 False 零回归）。"""

    def test_ctrl_r_disabled_retry_regression(self, tmp_path):
        """未启用反向搜索时 Ctrl+R 走 retry（Claude TUI parity 3.4 重映射）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            calls = []
            inp.set_special_key_callback(lambda a, t: calls.append(a) or t)
            os.write(w_fd, b"\x12")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert calls == ["retry"]
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_r_enabled_enters_search(self, tmp_path):
        """启用后 Ctrl+R 进入反向搜索（查询=当前缓冲，回调 active=True）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_reverse_search_enabled(True)
            syncs = []
            inp.set_reverse_search_callback(
                lambda q, m, i, a: syncs.append((q, m, i, a))
            )
            inp._history = ["hello world", "goodbye"]
            inp.handle_chars("hello")
            os.write(w_fd, b"\x12")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert syncs and syncs[0][3] is True
            assert syncs[0][0] == "hello"
            assert syncs[0][1] == ["hello world"]
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_r_empty_buffer_no_search(self, tmp_path):
        """启用后 Ctrl+R 且当前缓冲为空 → 不进入搜索（查询为空）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_reverse_search_enabled(True)
            syncs = []
            inp.set_reverse_search_callback(
                lambda q, m, i, a: syncs.append(a)
            )
            inp._history = ["hello world"]
            os.write(w_fd, b"\x12")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert syncs == []  # 未进入搜索 → 无状态同步
            assert not inp._buffer_editor.is_search_active()
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_r_advances_next_match(self, tmp_path):
        """启用后再次 Ctrl+R 推进到下一匹配（index 递增）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_reverse_search_enabled(True)
            syncs = []
            inp.set_reverse_search_callback(
                lambda q, m, i, a: syncs.append((q, m, i, a))
            )
            inp._history = ["m1", "m2"]
            inp.handle_chars("m")
            # 第一次 Ctrl+R：进入（idx=0）
            os.write(w_fd, b"\x12")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            # 第二次 Ctrl+R：推进（idx=1）
            os.write(w_fd, b"\x12")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert syncs[-1][3] is True
            assert syncs[-1][2] == 1
            assert syncs[-1][1] == ["m1", "m2"]
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_escape_exits_search_restores_buffer(self, tmp_path):
        """搜索模式 Esc 退出搜索并恢复原缓冲（不触发中断）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_reverse_search_enabled(True)
            syncs = []
            inp.set_reverse_search_callback(
                lambda q, m, i, a: syncs.append(a)
            )
            inp._history = ["hello world", "hello again"]
            inp.handle_chars("hello")
            os.write(w_fd, b"\x12")  # 进入搜索
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp._buffer_editor.is_search_active()
            os.write(w_fd, b"\x1b")  # Esc 退出搜索
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert not inp._buffer_editor.is_search_active()
            assert inp.get_current_text() == "hello"
            assert syncs[-1] is False  # active=False
            assert not inp.interrupted  # Esc 未触发中断
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_enter_in_search_applies_match(self, tmp_path):
        """搜索模式 Enter 应用匹配并退出（不提交）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_reverse_search_enabled(True)
            syncs = []
            inp.set_reverse_search_callback(
                lambda q, m, i, a: syncs.append(a)
            )
            inp._history = ["hello world"]
            inp.handle_chars("hel")
            os.write(w_fd, b"\x12")  # 进入搜索
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp._buffer_editor.is_search_active()
            os.write(w_fd, b"\r")  # Enter 应用匹配
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert not inp._buffer_editor.is_search_active()
            assert inp.get_current_text() == "hello world"
            assert not inp.has_queued_input()
            assert syncs[-1] is False
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_facade_setters_delegate(self, tmp_path):
        """外观 set_reverse_search_* 委托 InputDispatcher。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            cb = lambda q, m, i, a: None
            inp.set_reverse_search_enabled(True)
            inp.set_reverse_search_callback(cb)
            assert inp._dispatcher._reverse_search_enabled is True
            assert inp._dispatcher._reverse_search_callback is cb
        finally:
            os.close(fd)


class TestCtrlLScreenClear:
    """Claude TUI parity 步骤 3.1 — Ctrl+L 清屏分发。"""

    def test_ctrl_l_invokes_clear_screen_callback(self, tmp_path):
        """Ctrl+L（0x0c）→ 调用注入的 clear_screen 回调。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            calls = []
            inp.set_clear_screen_callback(lambda: calls.append(True))
            os.write(w_fd, b"\x0c")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert calls == [True]
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_l_no_callback_skips(self, tmp_path):
        """未注入 clear_screen 回调时 Ctrl+L 不抛异常（测试兼容）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            os.write(w_fd, b"\x0c")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True  # 不抛异常
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_l_skipped_while_streaming(self, tmp_path):
        """生成中（active_status=True）Ctrl+L 被忽略。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            calls = []
            inp.set_clear_screen_callback(lambda: calls.append(True))
            inp.set_active_status_callback(lambda: True)  # 生成中
            os.write(w_fd, b"\x0c")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert calls == []
        finally:
            os.close(w_fd)
            os.close(r_fd)


class TestCtrlDEOF:
    """Claude TUI parity 步骤 3.2 — Ctrl+D EOF 提交。"""

    def test_ctrl_d_empty_buffer_submits_exit(self, tmp_path):
        """空缓冲 Ctrl+D → 提交 "exit"（方向1 B2：不写入历史）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            os.write(w_fd, b"\x04")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_queued_input() == "exit"
            # 方向1 B2：Ctrl+D 空缓冲提交的 "exit" 不写入历史——内存历史为空
            # （写盘路径 _append_history_locked 未触发，_history_io.append 未调用）
            assert inp._buffer_editor._history == []
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_d_nonempty_buffer_noop(self, tmp_path):
        """非空缓冲 Ctrl+D → 无副作用（防误退）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("hello")
            os.write(w_fd, b"\x04")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_queued_input() is None
            assert inp.get_current_text() == "hello"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_ctrl_d_suppress_enter_regression(self, tmp_path):
        """方向2 — editmsg 选择期间（suppress_enter=True）空缓冲 Ctrl+D → no-op。

        修复前 Ctrl+D 绕过 Enter 抑制提交 "exit"（editmsg 选择期间误退出）。
        """
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_suppress_enter(True)
            os.write(w_fd, b"\x04")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            # 不提交 exit（editmsg 选择期间 Ctrl+D no-op）
            assert inp.get_queued_input() is None
            assert inp.get_current_text() == ""
        finally:
            os.close(w_fd)
            os.close(r_fd)


class TestRetryDraftPreservation:
    """方向1 B3 — Ctrl+R retry 不丢弃输入草稿。

    注入 special_key 回调返回 '/retry'，设置非空缓冲，调用
    ``_handle_special_key('retry')``：
    - 非空草稿：提交 /retry 后缓冲恢复为原草稿（供继续编辑）；
    - 空草稿：行为与现状一致（提交 /retry，缓冲为空）。
    """

    def test_retry_preserves_draft_after_submit(self, input_instance):
        """非空草稿触发 retry → 提交 /retry 后缓冲恢复为原草稿。"""
        inp = input_instance
        inp.handle_chars("draft text")
        cb = MagicMock(return_value="/retry")
        inp.set_special_key_callback(cb)
        inp._handle_special_key('retry')
        assert inp.get_queued_input() == "/retry"
        assert inp.get_current_text() == "draft text"

    def test_retry_empty_draft_unchanged(self, input_instance):
        """草稿为空时 retry → 提交 /retry，缓冲为空（行为与现状一致）。"""
        inp = input_instance
        cb = MagicMock(return_value="/retry")
        inp.set_special_key_callback(cb)
        inp._handle_special_key('retry')
        assert inp.get_queued_input() == "/retry"
        assert inp.get_current_text() == ""


class TestShiftEnterNewline:
    """Claude TUI parity 步骤 3.6 — Shift+Enter 换行（CSI u 13;2）。"""

    def test_shift_enter_inserts_newline_not_submit(self, tmp_path):
        """CSI u Shift+Enter → 缓冲插入换行，不触发提交。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            os.write(w_fd, b"\x1b[13;2u")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "\n"
            assert inp.get_queued_input() is None  # 未提交
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_shift_enter_appends_to_existing(self, tmp_path):
        """已有文本时 Shift+Enter 追加换行（不提交）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("abc")
            os.write(w_fd, b"\x1b[13;2u")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == "abc\n"
            assert inp.get_queued_input() is None
        finally:
            os.close(w_fd)
            os.close(r_fd)


# ═══════════════════════════════════════════════════════════
# 方向D 步骤16 — Esc 取消输入（配置门控，默认 False 零回归）
# ═══════════════════════════════════════════════════════════

class TestEscCancelInput:
    """InputDispatcher Esc 取消输入（配置门控）。

    语义：启用（set_esc_cancel_input(True)）+ 空闲（活跃状态 False）+
    缓冲非空时，单次 Esc 清空输入取消编辑（不触发中断）；否则走既有
    _do_interrupt（默认零回归）。
    """

    def test_esc_disabled_interrupt_regression(self, tmp_path):
        """未启用时 Esc → 中断（零回归）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.handle_chars("partial")
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.interrupted
            assert inp.get_current_text() == ""
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_esc_enabled_idle_nonempty_clears_buffer(self, tmp_path):
        """启用 + 空闲 + 非空缓冲 → 清空输入（不触发中断）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_esc_cancel_input(True)
            inp.set_active_status_callback(lambda: False)
            inp.handle_chars("draft text")
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == ""
            assert not inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_esc_enabled_empty_buffer_interrupt(self, tmp_path):
        """启用 + 缓冲为空 → 仍中断（双 Esc / 空输入语义）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_esc_cancel_input(True)
            inp.set_active_status_callback(lambda: False)
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_esc_enabled_generating_interrupt(self, tmp_path):
        """启用 + 生成中（活跃状态 True）→ 中断（不取消输入）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_esc_cancel_input(True)
            inp.set_active_status_callback(lambda: True)
            inp.handle_chars("draft")
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.interrupted
            assert inp.get_current_text() == ""
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_esc_enabled_status_callback_exception_treated_idle(self, tmp_path):
        """活跃状态回调抛异常 → 视为空闲（取消输入，不阻断）。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_esc_cancel_input(True)

            def _boom():
                raise RuntimeError("status cb boom")

            inp.set_active_status_callback(_boom)
            inp.handle_chars("draft")
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == ""
            assert not inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_esc_double_interrupt(self, tmp_path):
        """启用后首次 Esc 清空，再次 Esc（缓冲空）→ 中断。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "history")
            inp.set_esc_cancel_input(True)
            inp.set_active_status_callback(lambda: False)
            inp.handle_chars("draft")
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.get_current_text() == ""
            assert not inp.interrupted
            os.write(w_fd, b"\x1b")
            ready, _, _ = select.select([r_fd], [], [], 2.0)
            assert ready
            assert inp.read_stdin_once() is True
            assert inp.interrupted
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_facade_setters_delegate(self, tmp_path):
        """外观 set_esc_cancel_input / set_active_status_callback 委托。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            fn = lambda: True
            inp.set_esc_cancel_input(True)
            inp.set_active_status_callback(fn)
            assert inp._dispatcher._esc_cancel_input is True
            assert inp._dispatcher._active_status_fn is fn
        finally:
            os.close(fd)


# ═══════════════════════════════════════════════════════════
# 方向2 — editmsg 选择期间非确认键不触发 dismiss 确认 + Tab 不写缓冲
# ═══════════════════════════════════════════════════════════

class TestEditmsgKeyDismissGuard:
    """方向2 — editmsg 选择期间（_suppress_enter=True）backspace 等非确认键
    不触发 dismiss 回调（message_editor 将 dismiss 回调替换为确认信号——
    非确认键触发会提前确认选择）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_backspace_not_dismiss_when_suppress_enter(self, inp):
        """set_suppress_enter(True) 后 backspace 不调用 dismiss 回调。"""
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp.set_suppress_enter(True)
        inp.handle_chars("abc")
        inp._dispatch_key_event(KeyEvent(kind="backspace"))
        assert dismissed == []

    def test_backspace_dismiss_when_not_suppress(self, inp):
        """set_suppress_enter(False) 后 backspace 恢复调用 dismiss（零回归）。"""
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp.set_suppress_enter(False)
        inp.handle_chars("abc")
        inp._dispatch_key_event(KeyEvent(kind="backspace"))
        assert dismissed == [True]

    def test_home_end_delete_unknown_not_dismiss_when_suppress(self, inp):
        """suppress=True 时 home/end/delete/unknown 不调用 dismiss 回调。"""
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp.set_suppress_enter(True)
        inp.handle_chars("hello")
        for kind in ("home", "end", "delete", "unknown"):
            inp._dispatch_key_event(KeyEvent(kind=kind))
        assert dismissed == []

    def test_enter_still_dismisses_when_suppress(self, inp):
        """suppress=True 时 Enter 仍调用 dismiss（editmsg 确认机制不可改动）。"""
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp.set_suppress_enter(True)
        inp.handle_chars("hello")
        inp._dispatch_key_event(KeyEvent(kind="enter"))
        assert dismissed == [True]

    def test_cancel_input_dismisses(self, inp):
        """_cancel_input 调用 dismiss 回调（Esc 取消同时关闭补全弹窗）。"""
        dismissed = []
        inp.set_dismiss_completion_callback(lambda: dismissed.append(True))
        inp.set_suppress_enter(False)
        inp._dispatcher._cancel_input()
        assert dismissed == [True]

    def test_editmsg_tab_navigates_not_confirms(self, inp):
        """editmsg 模式（suppress=True）Tab 调用 navigate 回调（cycle），不写缓冲。"""
        navigated = []
        confirmed = []
        inp.set_completion_navigate_callback(
            lambda delta, text: navigated.append((delta, text)) or text
        )
        inp.set_completion_callback(
            lambda text: confirmed.append(text) or text
        )
        inp.set_suppress_enter(True)
        inp.handle_chars("hello")
        inp._dispatch_key_event(KeyEvent(kind="tab"))
        assert navigated == [(1, "hello")]  # 正向 cycle
        assert confirmed == []              # 不经 on_tab 确认
        assert inp.get_current_text() == "hello"  # 不写缓冲

    def test_editmsg_tab_no_navigate_callback(self, inp):
        """editmsg 模式无 navigate 回调时 Tab no-op（不抛异常）。"""
        inp.set_suppress_enter(True)
        inp.handle_chars("hello")
        inp._dispatch_key_event(KeyEvent(kind="tab"))  # 不抛异常
        assert inp.get_current_text() == "hello"

    def test_normal_tab_confirms_when_completion_visible(self, inp):
        """普通模式（suppress=False）Tab 保持确认行为（on_tab 路径）。"""
        navigated = []
        inp.set_completion_navigate_callback(
            lambda delta, text: navigated.append((delta, text)) or text
        )
        inp.set_suppress_enter(False)
        inp.handle_chars("hello")
        inp._dispatch_key_event(KeyEvent(kind="tab"))
        # 普通模式 Tab 走 _handle_tab（completion_callback 确认路径），不经 navigate
        assert navigated == []


class TestHandleCharsCarriageReturnFilter:
    """方向2 — handle_chars 过滤 \\r（粘贴文本 CR 不进入缓冲；\\n 保留）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_handle_chars_removes_cr_keeps_lf(self, inp):
        """handle_chars(\"a\\rb\\nc\") → 缓冲 \"ab\\nc\"（\\r 移除，\\n 保留）。"""
        inp.handle_chars("a\rb\nc")
        assert inp.get_current_text() == "ab\nc"

    def test_handle_chars_crlf_line_ending(self, inp):
        """CRLF 行尾（\\r\\n）→ 归一为 \\n（多行粘贴场景）。"""
        inp.handle_chars("line1\r\nline2\r\nline3")
        assert inp.get_current_text() == "line1\nline2\nline3"

    def test_handle_chars_plain_text_unchanged(self, inp):
        """纯文本（无 \\r）不受影响（回归）。"""
        inp.handle_chars("hello world")
        assert inp.get_current_text() == "hello world"

    def test_handle_chars_cr_only_removed(self, inp):
        """仅 \\r 的文本 → 空（不插入 CR）。"""
        inp.handle_chars("\r\r")
        assert inp.get_current_text() == ""


class TestHistoryIdxResetOnNavigation:
    """方向2 — _home/_end/_word_left/_word_right 重置 _history_idx（与其他编辑方法对齐）。"""

    @pytest.fixture
    def inp(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            yield Input(fd=fd, history_file=tmp_path / "history")
        finally:
            os.close(fd)

    def test_home_resets_history_idx(self, inp):
        """历史浏览中按 Home → _history_idx == -1。"""
        inp._history = ["stored"]
        inp._up()  # 进入历史导航
        assert inp._history_idx == 0
        inp._home()
        assert inp._history_idx == -1

    def test_end_resets_history_idx(self, inp):
        """历史浏览中按 End → _history_idx == -1。"""
        inp._history = ["stored"]
        inp._up()
        assert inp._history_idx == 0
        inp._end()
        assert inp._history_idx == -1

    def test_word_left_resets_history_idx(self, inp):
        """历史浏览中按 Ctrl+左 → _history_idx == -1。"""
        inp._history = ["stored"]
        inp._up()
        assert inp._history_idx == 0
        inp._word_left()
        assert inp._history_idx == -1

    def test_word_right_resets_history_idx(self, inp):
        """历史浏览中按 Ctrl+右 → _history_idx == -1。"""
        inp._history = ["stored"]
        inp._up()
        assert inp._history_idx == 0
        inp._word_right()
        assert inp._history_idx == -1

    def test_next_up_resaves_saved_input(self, inp):
        """重置后下次 _up 重新保存当前缓冲（语义保持：_saved_input_before_history 为当时缓冲）。"""
        inp._history = ["old", "older"]
        inp.handle_chars("current")
        inp._up()  # 进入历史导航
        assert inp._saved_input_before_history == "current"
        assert inp._buffer == "old"
        inp._home()  # 退出导航（_history_idx 重置，缓冲仍为历史条目内容）
        assert inp._history_idx == -1
        # 回到当前输入再 _up → 重新保存当前缓冲
        inp.set_buffer("current2")
        inp._up()
        assert inp._saved_input_before_history == "current2"
        assert inp._history_idx == 0
        assert inp._buffer == "old"
