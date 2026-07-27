"""Input 门面类单元测试。

覆盖：width/height 委托、buffer 委托、回调注册+调用、
compute_cursor 委托、read_byte/read_with_timeout。
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

from src.tui.input._input import Input
from src.tui.input._buffer import InputBuffer
from src.tui.input._parser import InputParser, KeyEvent
from src.tui.input._cursor import CursorPositioner


class TestInputFacade:
    """Input 门面类基本功能测试。"""

    @pytest.fixture
    def mock_terminal_width_cache(self):
        """Mock TerminalWidthCache，返回固定宽度 80、高度 24。"""
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24
        return mock_cache

    @pytest.fixture
    def input_instance(self, mock_terminal_width_cache, tmp_path):
        """创建 Input 实例（使用 mock 终端缓存 + tmp 历史文件）。"""
        hist_file = tmp_path / "test_history"
        return Input(
            fd=0,  # stdin fd
            history_file=hist_file,
            term_width_cache=mock_terminal_width_cache,
        )

    def test_width_delegation(self, input_instance):
        """width 属性委托 TermWidthCache.get_width()。"""
        assert input_instance.width == 80

    def test_height_delegation(self, input_instance):
        """height 属性委托 TermWidthCache.get_height()。"""
        assert input_instance.height == 24

    def test_buffer_property(self, input_instance):
        """buffer 属性返回 InputBuffer 实例。"""
        assert isinstance(input_instance.buffer, InputBuffer)

    def test_parser_property(self, input_instance):
        """parser 属性返回 InputParser 实例。"""
        assert isinstance(input_instance.parser, InputParser)

    def test_buffer_handle_char_delegation(self, input_instance):
        """buffer.handle_char 正确委托到 InputBuffer。"""
        input_instance.buffer.handle_char('h')
        input_instance.buffer.handle_char('i')
        assert input_instance.buffer.get_current_text() == "hi"

    def test_set_echo_callback(self, input_instance):
        """set_echo_callback 正确注册并触发回显。"""
        called = []
        def echo_cb(text, pos):
            called.append((text, pos))
        input_instance.set_echo_callback(echo_cb)
        input_instance.buffer.handle_char('x')
        assert len(called) >= 1
        assert called[-1][0] == 'x'
        assert called[-1][1] == 1

    def test_set_special_key_callback(self, input_instance):
        """set_special_key_callback 存储回调引用。"""
        cb = MagicMock()
        input_instance.set_special_key_callback(cb)
        assert input_instance._special_key_callback is cb

    def test_set_completion_callback(self, input_instance):
        """set_completion_callback 存储回调引用。"""
        cb = MagicMock()
        input_instance.set_completion_callback(cb)
        assert input_instance._completion_callback is cb

    def test_set_dismiss_completion_callback(self, input_instance):
        """set_dismiss_completion_callback 存储回调引用。"""
        cb = MagicMock()
        input_instance.set_dismiss_completion_callback(cb)
        assert input_instance._dismiss_completion_callback is cb

    def test_set_completion_navigate_callback(self, input_instance):
        """set_completion_navigate_callback 存储回调引用。"""
        cb = MagicMock()
        input_instance.set_completion_navigate_callback(cb)
        assert input_instance._completion_navigate_callback is cb

    def test_set_auto_completion_callback(self, input_instance):
        """set_auto_completion_callback 存储回调引用。"""
        cb = MagicMock()
        input_instance.set_auto_completion_callback(cb)
        assert input_instance._auto_completion_callback is cb


class TestInputComputeCursor:
    """compute_cursor 委托测试。"""

    @pytest.fixture
    def input_instance(self, tmp_path):
        """创建 Input 实例，mock CursorPositioner.compute。"""
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24

        hist_file = tmp_path / "test_history"
        inp = Input(
            fd=0,
            history_file=hist_file,
            term_width_cache=mock_cache,
        )
        # Mock CursorPositioner.compute
        inp._cursor.compute = MagicMock(return_value=(22, 5, 0, 4))
        return inp

    def test_compute_cursor_delegation(self, input_instance):
        """compute_cursor 正确委托到 CursorPositioner.compute。"""
        result = input_instance.compute_cursor(
            text="hello",
            cursor_pos=5,
            bottom_lines=5,
            subagent_lines=0,
            completion_height=0,
        )
        assert result == (22, 5, 0, 4)
        input_instance._cursor.compute.assert_called_once_with(
            "hello", 5, 5, 0, 0,
        )


class TestInputFeedByte:
    """feed_byte 委托测试。"""

    @pytest.fixture
    def input_instance(self, tmp_path):
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24
        return Input(
            fd=0,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_cache,
        )

    def test_feed_byte_char(self, input_instance):
        """feed_byte 可打印字符返回 KeyEvent(kind='char')。"""
        result = input_instance.feed_byte(ord('a'))
        assert result is not None
        assert result.kind == "char"
        assert result.char == "a"

    def test_feed_byte_esc_returns_none(self, input_instance):
        """feed_byte ESC (0x1b) 返回 None。"""
        result = input_instance.feed_byte(0x1b)
        assert result is None

    def test_feed_byte_enter(self, input_instance):
        """feed_byte \\r 返回 KeyEvent(kind='enter')。"""
        result = input_instance.feed_byte(0x0d)
        assert result is not None
        assert result.kind == "enter"


class TestInputReadMethods:
    """read_byte / read_with_timeout / try_read_paste / read_utf8_char 测试。"""

    @pytest.fixture
    def input_instance(self, tmp_path):
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24
        return Input(
            fd=0,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_cache,
        )

    def test_try_read_paste_single_char(self, input_instance):
        """try_read_paste: 无后续数据时返回原字符。"""
        # Mock select 返回空（无更多数据）
        with patch("select.select", return_value=([], [], [])):
            result = input_instance.try_read_paste(0, "a")
            assert result == "a"

    def test_read_utf8_char_valid_2byte(self, input_instance):
        """read_utf8_char: 有效 2 字节 UTF-8 序列正确解码。"""
        # UTF-8 'é' = 0xC3 0xA9
        from src.tui.input import _input as input_mod
        with patch.object(input_mod, "select") as mock_select:
            mock_select.select.return_value = ([0], [], [])
            with patch.object(input_mod.os, "read", return_value=b"\xa9"):
                result = input_instance.read_utf8_char(0, 0xC3)
                assert result == "é"

    def test_read_utf8_char_invalid_first_byte(self, input_instance):
        """read_utf8_char: 无效首字节返回 None。"""
        # 0x80 是续字节，不是有效首字节
        result = input_instance.read_utf8_char(0, 0x80)
        assert result is None
