"""InputBuffer 单元测试。

覆盖：基本插入、批量插入、退格、Enter 提交、历史导航、
Home/End、词边界移动、删除操作、drain_all、历史加载。
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.tui.input._buffer import InputBuffer


class TestInputBufferBasic:
    """基本字符插入和回显回调测试。"""

    def test_handle_char_insert(self):
        """handle_char 基本插入 + 光标移动到末尾。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_char('h')
        buf.handle_char('i')
        assert buf.get_current_text() == "hi"

    def test_handle_char_cursor_pos(self):
        """handle_char 插入后光标位置正确。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_char('a')
        # 通过回显回调验证光标位置
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_char('b')
        assert received[-1] == ("ab", 2)

    def test_handle_char_filter_control(self):
        """handle_char 过滤不可打印控制字符。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        # 先插入正常字符
        buf.handle_char('x')
        received.clear()
        # 控制字符应被静默忽略，不触发回显
        buf.handle_char('\x03')  # Ctrl+C
        assert received == []
        assert buf.get_current_text() == "x"

    def test_handle_chars_batch(self):
        """handle_chars 批量插入（粘贴场景），只触发一次回显。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_chars("hello world")
        assert len(received) == 1
        assert received[0] == ("hello world", 11)

    def test_handle_chars_multiline_paste(self):
        """handle_chars 粘贴含换行的文本。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("line1\nline2")
        assert buf.get_current_text() == "line1\nline2"


class TestBackspace:
    """退格操作测试。"""

    def test_backspace_simple(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abc")
        buf._backspace()
        assert buf.get_current_text() == "ab"

    def test_backspace_cursor_pos(self):
        """退格后光标位置正确。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abc")
        buf._left()  # 光标移到 'c' 前
        buf._left()  # 光标移到 'a' 后（位置 1）
        buf._backspace()  # 删除 'a'
        assert buf.get_current_text() == "bc"

    def test_backspace_at_start(self):
        """光标在开头时退格无操作。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abc")
        # 光标已在末尾（位置3），移到开头
        buf._left()
        buf._left()
        buf._left()  # 位置 0
        buf._backspace()
        assert buf.get_current_text() == "abc"

    def test_backspace_exits_history(self):
        """历史导航模式下退格退出导航。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["old1", "old2"]
        buf._history_idx = 0
        buf._buffer = "old1"
        buf._cursor_pos = 4
        buf._backspace()
        assert buf._history_idx == -1
        assert buf.get_current_text() == "old"


class TestEnter:
    """Enter 提交测试。"""

    def test_enter_submit(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello")
        with patch("src.tui.input._buffer._append_to_history_file", return_value=True):
            buf._enter()
        assert buf.get_queued_input() == "hello"
        assert buf.get_current_text() == ""

    def test_enter_idempotent(self):
        """Enter 幂等：连续两次 Enter（中间未消费）不覆盖已提交文本。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello")
        with patch("src.tui.input._buffer._append_to_history_file", return_value=True):
            buf._enter()
            # 第二次 Enter 在未消费第一次提交前调用，应静默返回
            buf._enter()
        first = buf.get_queued_input()
        assert first == "hello"
        # 确认第二次 Enter 未覆盖已提交文本
        second = buf.get_queued_input()
        assert second is None

    def test_enter_adds_to_history(self):
        """Enter 添加输入到历史（mock 文件写入以隔离真实历史文件）。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("test line")
        with patch("src.tui.input._buffer._append_to_history_file", return_value=True):
            buf._enter()
        assert "test line" in buf._history


class TestHistoryNavigation:
    """_up / _down 历史导航测试。"""

    def test_up_enter_history(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["recent", "older"]
        # 初始状态
        buf.handle_chars("current")
        buf._up()
        assert buf.get_current_text() == "recent"
        assert buf._history_idx == 0

    def test_up_down_cycle(self):
        """上下箭头循环：up→down 回到原始输入。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["item1", "item2"]
        buf.handle_chars("original")
        buf._up()        # → "item1"
        assert buf.get_current_text() == "item1"
        buf._down()      # → "original" (退出导航)
        assert buf.get_current_text() == "original"
        assert buf._history_idx == -1

    def test_up_then_edit_exits_history(self):
        """历史浏览中编辑退出导航。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["stored"]
        buf._up()  # 进入历史导航
        assert buf._history_idx == 0
        buf.handle_char('!')  # 编辑
        assert buf._history_idx == -1

    def test_up_empty_history(self):
        """空历史时 _up 无操作。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("text")
        buf._up()
        assert buf.get_current_text() == "text"

    def test_down_no_navigation(self):
        """非导航模式下 _down 无操作。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("text")
        buf._down()
        assert buf.get_current_text() == "text"


class TestHomeEnd:
    """Home / End 测试。"""

    def test_home_single_line(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abcdef")
        buf._home()
        # 光标在开头，通过回显验证
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf._home()  # 已在开头，再次 home 不变
        assert received[-1][1] == 0

    def test_end_single_line(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abcdef")
        buf._home()  # 光标到开头
        buf._end()   # 光标到末尾
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_char('!')
        assert received[-1] == ("abcdef!", 7)

    def test_home_multiline(self):
        """多行文本：Home 跳到当前逻辑行首。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("line1\nline2")
        # 光标在末尾 (位置 11), Home 应该跳到 "line2" 的行首即位置 6
        buf._home()
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_char('>')
        assert "line1\n>line2" in buf.get_current_text()


class TestWordMovement:
    """_word_left / _word_right 词边界移动测试。"""

    def test_word_left_basic(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello world")
        # 光标在末尾 (位置 11)
        buf._word_left()
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_char('x')
        # 光标应在 "world" 的 'w' 位置 (6)，插入 'x' 后光标到 7
        assert buf.get_current_text().startswith("hello xworld")

    def test_word_right_basic(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello world")
        # 光标移到开头
        for _ in range(11):
            buf._left()
        buf._word_right()
        received = []

        def cb(text, pos):
            received.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_char('>')
        assert "hello >world" in buf.get_current_text()


class TestDeleteOperations:
    """删除操作测试。"""

    def test_delete_middle(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abc")
        buf._left()  # 光标在 'c' 前
        buf._delete()
        assert buf.get_current_text() == "ab"

    def test_delete_at_end(self):
        """光标在末尾时 Del 无操作。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("abc")
        buf._delete()
        assert buf.get_current_text() == "abc"

    def test_delete_word_left(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello world")
        buf._delete_word_left()
        assert buf.get_current_text() == "hello "

    def test_kill_to_bol(self):
        """Ctrl+U：从光标删除到当前逻辑行首。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello world")
        # 光标目前在末尾（位置 11），需要移到 "world" 的 'w' 处（位置 6）
        for _ in range(5):
            buf._left()
        # 现在光标在位置 6，"hello world"[6] = 'w'
        buf._kill_to_bol()
        # 删除 "hello " 后应剩下 "world"
        assert buf.get_current_text() == "world"

    def test_kill_to_eol(self):
        """Ctrl+K：从光标删除到当前逻辑行尾。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("hello world")
        # 光标移到开头
        buf._home()
        buf._kill_to_eol()
        assert buf.get_current_text() == ""


class TestDrainAll:
    """drain_all 原子排出测试。"""

    def test_drain_all_submitted(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("submitted")
        with patch("src.tui.input._buffer._append_to_history_file", return_value=True):
            buf._enter()
        submitted, buffer_text = buf.drain_all()
        assert submitted == "submitted"
        assert buffer_text == ""
        assert buf.get_current_text() == ""

    def test_drain_all_no_submit(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("draft")
        submitted, buffer_text = buf.drain_all()
        assert submitted is None
        assert buffer_text == "draft"
        assert buf.get_current_text() == ""

    def test_drain_all_resets_history(self):
        """drain_all 重置历史导航状态。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["old"]
        buf._history_idx = 0
        buf._saved_input_before_history = "original"
        buf.drain_all()
        assert buf._history_idx == -1
        assert buf._saved_input_before_history == ""


class TestSetBuffer:
    """set_buffer 测试。"""

    def test_set_buffer_prefill(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.set_buffer("prefill text")
        assert buf.get_current_text() == "prefill text"

    def test_set_buffer_clears_submitted(self):
        """set_buffer 清除残留的提交状态。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf.handle_chars("old")
        with patch("src.tui.input._buffer._append_to_history_file", return_value=True):
            buf._enter()  # 提交
        # 不清除，直接 set_buffer
        buf.set_buffer("new")
        assert buf.get_queued_input() is None  # 残留被清除


class TestUnescape:
    """_unescape 静态方法测试。"""

    def test_unescape_newline(self):
        assert InputBuffer._unescape(r"hello\nworld") == "hello\nworld"

    def test_unescape_no_escape(self):
        assert InputBuffer._unescape("plain text") == "plain text"

    def test_unescape_multiple(self):
        assert InputBuffer._unescape(r"a\nb\nc") == "a\nb\nc"


class TestHistoryIndicator:
    """get_history_indicator 测试。"""

    def test_no_navigation(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        assert buf.get_history_indicator() == ""

    def test_navigation_mode(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["a", "b", "c"]
        buf._history_idx = 1
        assert "历史 2/3" in buf.get_history_indicator()


class TestLoadHistory:
    """load_history 去重 + 合并测试。"""

    def test_load_history_empty_file(self):
        """空历史文件不报错，保留空历史。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        with patch("src.tui.input._buffer._read_history_file", return_value=("", False)):
            buf.load_history()
        assert buf._history == []

    def test_load_history_from_mock_file(self):
        """load_history 从 mock 文件加载，正确反转顺序。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        with patch("src.tui.input._buffer._read_history_file",
                   return_value=("line1\nline2\nline3\n", True)):
            with patch("src.tui.input._buffer._compact_history_file"):
                buf.load_history()
        # 反转: 最近的在 index=0
        assert buf._history[0] == "line3"
        assert len(buf._history) == 3

    def test_load_history_merge_existing(self):
        """load_history 合并到已有内存历史。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["mem_entry"]
        with patch("src.tui.input._buffer._read_history_file",
                   return_value=("file_entry\n", True)):
            with patch("src.tui.input._buffer._compact_history_file"):
                buf.load_history()
        assert "mem_entry" in buf._history
        assert "file_entry" in buf._history


class TestEchoCallback:
    """回显回调测试。"""

    def test_echo_callback_registration(self):
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        calls = []

        def cb(text, pos):
            calls.append((text, pos))
        buf.set_echo_callback(cb)
        buf.handle_char('x')
        assert len(calls) == 1
        assert calls[0] == ("x", 1)

    def test_echo_with_history_indicator(self):
        """历史导航模式下回显包含指示器。"""
        buf = InputBuffer(history_file=Path("/tmp/test_history"))
        buf._history = ["hist1", "hist2"]
        buf._history_idx = 0
        buf._buffer = "hist1"
        buf._cursor_pos = 5
        calls = []

        def cb(text, pos):
            calls.append((text, pos))
        buf.set_echo_callback(cb)
        buf._echo("hist1")
        assert "历史 1/2" in calls[0][0]
        # 光标位置仍为原始文本长度
        assert calls[0][1] == 5
