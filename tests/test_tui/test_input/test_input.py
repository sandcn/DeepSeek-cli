"""Input 类单元测试（适配统一 Input 类）。

覆盖：width/height 委托、缓冲委托、回调注册+调用、
compute_cursor、I/O 方法、start_io/stop_io。
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.tui.input import Input, KeyEvent


class TestInputFacade:
    """Input 类基本功能测试。"""

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
        fd = os.open("/dev/null", os.O_RDONLY)
        return Input(
            fd=fd,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_terminal_width_cache,
        )

    def test_width_delegation(self, input_instance):
        """width 属性委托 TermWidthCache.get_width()。"""
        assert input_instance.width == 80

    def test_height_delegation(self, input_instance):
        """height 属性委托 TermWidthCache.get_height()。"""
        assert input_instance.height == 24

    def test_handle_char(self, input_instance):
        """handle_char 正确插入字符到缓冲区。"""
        input_instance.handle_char('h')
        input_instance.handle_char('i')
        assert input_instance.get_current_text() == "hi"

    def test_set_echo_callback(self, input_instance):
        """set_echo_callback 正确注册并触发回显。"""
        called = []
        def echo_cb(text, pos):
            called.append((text, pos))
        input_instance.set_echo_callback(echo_cb)
        input_instance.handle_char('x')
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
    """compute_cursor 测试。"""

    @pytest.fixture
    def input_instance(self, tmp_path):
        """创建 Input 实例，mock compute_cursor 方法。"""
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24

        fd = os.open("/dev/null", os.O_RDONLY)
        inp = Input(
            fd=fd,
            history_file=tmp_path / "test_history",
            term_width_cache=mock_cache,
        )
        return inp

    def test_compute_cursor_returns_tuple(self, input_instance):
        """compute_cursor 返回四元组。"""
        result = input_instance.compute_cursor(
            text="hello",
            cursor_pos=5,
            bottom_lines=5,
            subagent_lines=0,
            completion_height=0,
        )
        assert isinstance(result, tuple)
        assert len(result) == 4
        r_cursor, cursor_col, vis_row, vis_col = result
        assert isinstance(r_cursor, int)
        assert isinstance(cursor_col, int)
        assert isinstance(vis_row, int)
        assert isinstance(vis_col, int)
        assert 1 <= r_cursor <= input_instance.height
        assert 1 <= cursor_col <= input_instance.width


class TestInputFeedByte:
    """feed_byte 委托测试。"""

    @pytest.fixture
    def input_instance(self, tmp_path):
        mock_cache = MagicMock()
        mock_cache.get_width.return_value = 80
        mock_cache.get_height.return_value = 24
        fd = os.open("/dev/null", os.O_RDONLY)
        return Input(
            fd=fd,
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
        fd = os.open("/dev/null", os.O_RDONLY)
        return Input(
            fd=fd,
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
        with patch("select.select", return_value=([0], [], [])):
            with patch("os.read", return_value=b"\xa9"):
                result = input_instance.read_utf8_char(0, 0xC3)
                assert result == "é"

    def test_read_utf8_char_invalid_first_byte(self, input_instance):
        """read_utf8_char: 无效首字节返回 None。"""
        # 0x80 是续字节，不是有效首字节
        result = input_instance.read_utf8_char(0, 0x80)
        assert result is None


class TestInputIOLifecycle:
    """start_io / stop_io 生命周期测试。"""

    @pytest.fixture
    def input_instance(self, tmp_path):
        fd = os.open("/dev/null", os.O_RDONLY)
        return Input(
            fd=fd,
            history_file=tmp_path / "test_history",
        )

    def test_is_io_running_initially_false(self, input_instance):
        assert input_instance.is_io_running is False

    def test_start_stop_io(self, input_instance):
        import time
        input_instance.start_io()
        try:
            assert input_instance.is_io_running is True
        finally:
            input_instance.stop_io()
        time.sleep(0.1)
        assert input_instance.is_io_running is False

    def test_stop_io_idempotent(self, input_instance):
        input_instance.stop_io()  # 未启动时调用应安全
        assert input_instance.is_io_running is False

    def test_pause_resume_io(self, input_instance):
        import time
        input_instance.start_io()
        try:
            input_instance.pause_io()
            time.sleep(0.05)
            assert not input_instance._active.is_set()
            input_instance.resume_io()
            assert input_instance._active.is_set()
        finally:
            input_instance.stop_io()
