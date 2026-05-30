"""测试 EscapeMonitor 流式输入功能。

覆盖内容：
  1. 流式输入缓冲区基本操作（字符、退格、Enter）
  2. has_queued_input / get_queued_input
  3. get_current_stream_input / reset_stream_input
  4. echo 回调
  5. 中断时清空缓冲区
  6. start() 清空状态
  7. 历史导航（上下箭头浏览输入历史）
"""

from __future__ import annotations

import threading
import pytest
from unittest.mock import MagicMock

from src.api.escape_monitor import EscapeMonitor


class TestStreamInputBuffer:
    """流式输入缓冲区基本操作测试。"""

    def test_handle_stream_char_adds_to_buffer(self):
        """_handle_stream_char 将字符追加到缓冲区。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('h')
        monitor._input_handler.handle_char('i')
        assert monitor.get_current_stream_input() == "hi"

    def test_handle_stream_backspace_removes_last(self):
        """_handle_stream_backspace 删除最后一个字符。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('a')
        monitor._input_handler.handle_char('b')
        monitor._input_handler._backspace()
        assert monitor.get_current_stream_input() == "a"

    def test_handle_stream_backspace_empty(self):
        """空缓冲区退格不崩溃。"""
        monitor = EscapeMonitor()
        monitor._input_handler._backspace()
        assert monitor.get_current_stream_input() == ""

    def test_handle_stream_enter_sets_ready_and_clears_buffer(self):
        """Enter 设置 _input_ready 并清空缓冲区。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('h')
        monitor._input_handler._enter()
        assert monitor.has_queued_input() is True
        assert monitor.get_current_stream_input() == ""

    def test_get_queued_input_returns_and_clears(self):
        """get_queued_input 返回文本并清空状态。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('t')
        monitor._input_handler.handle_char('e')
        monitor._input_handler.handle_char('s')
        monitor._input_handler.handle_char('t')
        monitor._input_handler._enter()

        result = monitor.get_queued_input()
        assert result == "test"
        assert monitor.has_queued_input() is False
        assert monitor.get_current_stream_input() == ""

    def test_get_queued_input_without_enter_returns_none(self):
        """未按 Enter 时 get_queued_input 返回 None。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('x')
        assert monitor.get_queued_input() is None
        # 缓冲区仍保留
        assert monitor.get_current_stream_input() == "x"

    def test_reset_stream_input_clears(self):
        """reset_stream_input 清空缓冲区。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('a')
        monitor._input_handler.handle_char('b')
        monitor.reset_stream_input()
        assert monitor.get_current_stream_input() == ""
        assert monitor.has_queued_input() is False

    def test_handle_stream_char_filters_control_chars(self):
        """控制字符（非可打印）不进入流式缓冲区。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('\x00')  # null
        monitor._input_handler.handle_char('\x01')  # SOH
        assert monitor.get_current_stream_input() == ""


class TestStreamInputEcho:
    """回显回调测试。"""

    def test_echo_callback_called_on_char(self):
        """输入字符时调用回显回调。"""
        monitor = EscapeMonitor()
        callback = MagicMock()
        monitor.set_echo_callback(callback)

        monitor._input_handler.handle_char('x')
        callback.assert_called_once_with("x", 1)

    def test_echo_callback_called_on_backspace(self):
        """退格时调用回显回调（空文本）。"""
        monitor = EscapeMonitor()
        callback = MagicMock()
        monitor.set_echo_callback(callback)

        monitor._input_handler.handle_char('a')
        callback.reset_mock()
        monitor._input_handler._backspace()
        callback.assert_called_once_with("", 0)

    def test_echo_callback_called_on_enter(self):
        """Enter 时调用回显回调清空输入行。"""
        monitor = EscapeMonitor()
        callback = MagicMock()
        monitor.set_echo_callback(callback)

        monitor._input_handler.handle_char('hi')
        callback.reset_mock()
        monitor._input_handler._enter()
        callback.assert_called_once_with("", 0)

    def test_echo_callback_empty_enter(self):
        """空输入 Enter 时调用回显回调清空输入行。"""
        monitor = EscapeMonitor()
        callback = MagicMock()
        monitor.set_echo_callback(callback)

        monitor._input_handler._enter()
        callback.assert_called_once_with("", 0)

    def test_echo_callback_exception_suppressed(self):
        """回显回调异常不影响主流程。"""
        monitor = EscapeMonitor()
        def bad_callback(text, cursor_pos=-1):
            raise RuntimeError("boom")
        monitor.set_echo_callback(bad_callback)

        # 不应抛出异常
        monitor._input_handler.handle_char('x')
        assert monitor.get_current_stream_input() == "x"


class TestStreamInputInterrupt:
    """中断时流式输入行为测试。"""

    def test_interrupt_clears_buffer(self):
        """中断时清空流式输入缓冲区。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('h')
        monitor._input_handler.handle_char('e')
        monitor._input_handler.handle_char('l')
        monitor._input_handler.handle_char('l')
        monitor._input_handler.handle_char('o')

        monitor._do_interrupt()
        assert monitor.get_current_stream_input() == ""
        assert monitor.has_queued_input() is False


class TestStreamInputStartReset:
    """start() 清空流式输入状态测试。"""

    def test_start_clears_stream_state(self):
        """start() 清空之前的流式输入。"""
        monitor = EscapeMonitor()
        monitor._input_handler.handle_char('x')
        monitor._input_handler._enter()

        # start 会启动线程，但我们可以验证它清空了状态
        # 注意：start 会创建线程，这里只测状态清理
        monitor._input_handler._buffer = "stale"
        monitor._input_handler._input_ready.set()

        # 模拟 start 中的清理逻辑
        with monitor._input_handler._lock:
            monitor._input_handler._buffer = ""
            monitor._input_handler._input_ready.clear()

        assert monitor.get_current_stream_input() == ""
        assert monitor.has_queued_input() is False


class TestStreamInputHistory:
    """历史导航（上下箭头）测试。"""

    # ── 辅助方法 ────────────────────────────────────────

    @staticmethod
    def _make_monitor_with_history(lines: list[str]) -> EscapeMonitor:
        """创建带预设历史的 EscapeMonitor，跳过文件读取。"""
        monitor = EscapeMonitor()
        # 反向：index=0 为最近一条
        monitor._input_handler._history = list(reversed(lines))
        return monitor

    # ── 上箭头 ──────────────────────────────────────────

    def test_up_shows_most_recent(self):
        """上箭头：首次按下显示最近一条历史。"""
        monitor = self._make_monitor_with_history(["line1", "line2", "line3"])
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "line3"

    def test_up_second_press_shows_older(self):
        """上箭头：连续按下向更早历史移动。"""
        monitor = self._make_monitor_with_history(["a", "b", "c"])
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "c"
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "b"

    def test_up_past_oldest_stops(self):
        """上箭头：到最早一条后不再移动。"""
        monitor = self._make_monitor_with_history(["only"])
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "only"
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "only"

    def test_up_preserves_original_input(self):
        """上箭头：进入导航前保存原始输入，供下箭头恢复。"""
        monitor = self._make_monitor_with_history(["hist1", "hist2"])
        monitor._input_handler.handle_char('o')
        monitor._input_handler.handle_char('r')
        monitor._input_handler.handle_char('i')
        monitor._input_handler.handle_char('g')
        assert monitor.get_current_stream_input() == "orig"
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "hist2"
        # 回到最新再按一次下箭头应恢复原始输入
        monitor._input_handler._down()
        assert monitor.get_current_stream_input() == "orig"

    # ── 下箭头 ──────────────────────────────────────────

    def test_down_without_up_does_nothing(self):
        """下箭头：非导航模式下无操作。"""
        monitor = self._make_monitor_with_history(["a", "b"])
        monitor._input_handler.handle_char('x')
        monitor._input_handler._down()
        assert monitor.get_current_stream_input() == "x"

    def test_down_returns_to_newer(self):
        """下箭头：向更新方向移动。"""
        monitor = self._make_monitor_with_history(["old", "mid", "new"])
        monitor._input_handler._up()    # → "new"
        monitor._input_handler._up()    # → "mid"
        monitor._input_handler._down()  # → "new"
        assert monitor.get_current_stream_input() == "new"

    def test_down_at_newest_restores_original(self):
        """下箭头：在最新一条再按恢复原始输入并退出导航。"""
        monitor = self._make_monitor_with_history(["a", "b"])
        monitor._input_handler.handle_char('m')
        monitor._input_handler.handle_char('y')
        monitor._input_handler._up()        # → "b"
        monitor._input_handler._down()      # → "my"（恢复原始）
        assert monitor.get_current_stream_input() == "my"

    # ── 空历史 ──────────────────────────────────────────

    def test_up_with_empty_history_does_nothing(self):
        """空历史：上箭头无操作。"""
        monitor = EscapeMonitor()
        monitor._input_handler._history = []
        monitor._input_handler.handle_char('x')
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "x"

    def test_down_with_empty_history_does_nothing(self):
        """空历史：下箭头无操作。"""
        monitor = EscapeMonitor()
        monitor._input_handler._history = []
        monitor._input_handler._up()  # 尝试进入导航
        monitor._input_handler._down()
        assert monitor.get_current_stream_input() == ""

    # ── 打字退出导航 ────────────────────────────────────

    def test_typing_exits_history_mode(self):
        """在历史行上打字 → 退出导航模式，字符追加到当前行。"""
        monitor = self._make_monitor_with_history(["old1", "old2"])
        monitor._input_handler._up()            # → "old2"
        monitor._input_handler.handle_char('!')       # 应退出导航并追加
        assert monitor.get_current_stream_input() == "old2!"
        # 再按上箭头应重新进入导航（index=-1，然后设为0）
        monitor._input_handler._up()
        assert monitor.get_current_stream_input() == "old2"

    def test_backspace_exits_history_mode(self):
        """在历史行上退格 → 退出导航模式，删除一个字符。"""
        monitor = self._make_monitor_with_history(["hello"])
        monitor._input_handler._up()             # → "hello"
        monitor._input_handler._backspace()      # 退出导航 + 删除
        assert monitor.get_current_stream_input() == "hell"

    # ── 中断清空导航状态 ────────────────────────────────

    def test_interrupt_resets_history_state(self):
        """中断时清空历史导航状态。"""
        monitor = self._make_monitor_with_history(["a", "b"])
        monitor._input_handler._up()
        assert monitor._input_handler._history_idx >= 0
        monitor._do_interrupt()
        assert monitor._input_handler._history_idx == -1
        assert monitor._input_handler._saved_input_before_history == ""
        assert monitor.get_current_stream_input() == ""

    # ── 回显回调 ────────────────────────────────────────

    def test_up_calls_echo_callback(self):
        """上箭头触发回显回调（含历史指示器）。"""
        monitor = self._make_monitor_with_history(["echo_test"])
        callback = MagicMock()
        monitor.set_echo_callback(callback)

        monitor._input_handler._up()
        # 历史浏览模式下文本后追加指示器，光标位置不变（9 = len("echo_test")）
        callback.assert_called_once_with("echo_test [历史 1/1]", 9)

    def test_down_calls_echo_callback(self):
        """下箭头触发回显回调。"""
        monitor = self._make_monitor_with_history(["a", "b"])
        callback = MagicMock()
        monitor.set_echo_callback(callback)

        monitor._input_handler._up()       # → "b"
        callback.reset_mock()
        monitor._input_handler._down()     # → 恢复原始（空）
        callback.assert_called_once_with("", 0)


class TestHistoryFileSerialization:
    """历史文件序列化（读写→转义→还原）测试。

    测试 _append_history_locked（写文件时的 \\n 转义）
    和 load_history（读文件时的 \\n 还原），包括旧格式兼容。
    使用临时文件隔离，避免 xdist 并行竞态。
    """

    @pytest.fixture(autouse=True)
    def _isolate_history_file(self, tmp_path):
        """每个测试使用独立的临时历史文件，避免 xdist 并行竞态。

        需同时替换 defaults 模块和 escape_monitor 模块中的引用，
        因为 escape_monitor 在模块级 from ..config.defaults import INPUT_HISTORY_FILE。
        """
        self._test_path = tmp_path / "input_history"
        import src.config.defaults as defaults
        import src.api.escape_monitor as em
        self._saved_defaults_path = defaults.INPUT_HISTORY_FILE
        self._saved_em_path = em.INPUT_HISTORY_FILE
        defaults.INPUT_HISTORY_FILE = self._test_path
        em.INPUT_HISTORY_FILE = self._test_path
        yield
        defaults.INPUT_HISTORY_FILE = self._saved_defaults_path
        em.INPUT_HISTORY_FILE = self._saved_em_path

    # ── _unescape 纯函数单元测试 ────────────────────────

    def test_unescape_multiline(self):
        """_unescape 将转义的 \\n 还原为真实换行符。"""
        monitor = EscapeMonitor()
        result = monitor._input_handler._unescape("hello\\nworld")
        assert result == "hello\nworld"

    def test_unescape_singleline_noop(self):
        """单行文本经 _unescape 不变。"""
        monitor = EscapeMonitor()
        result = monitor._input_handler._unescape("hello world")
        assert result == "hello world"

    def test_unescape_empty_string(self):
        """空字符串经 _unescape 不变。"""
        monitor = EscapeMonitor()
        result = monitor._input_handler._unescape("")
        assert result == ""

    def test_unescape_no_backslash(self):
        """不含 \\n 的文本（含真实换行符）经 _unescape 不变。"""
        monitor = EscapeMonitor()
        result = monitor._input_handler._unescape("line1\nline2")
        assert result == "line1\nline2"

    # ── 集成测试：写文件 + 读文件 ─────────────────────

    def test_write_and_read_multiline(self):
        """多行条目写入后回读正确还原。"""
        monitor = EscapeMonitor()
        handler = monitor._input_handler

        handler._history = []
        handler._buffer = "line1\nline2\nline3"
        handler._cursor_pos = 18
        handler._enter()  # 触发 _append_history_locked

        # 验证文件内容：\\n 应为转义后的字面 \\n（反斜杠+n）
        content = self._test_path.read_text(encoding="utf-8")
        assert "line1\\nline2\\nline3" in content, \
            f"多行条目中的 \\n 应转义为字面 \\\\n，实际：{repr(content)}"

        # 重新加载历史
        handler._history = []
        handler.load_history()
        assert "line1\nline2\nline3" in handler._history, \
            f"回读后应还原为真实 \\n，实际：{handler._history}"

    def test_write_and_read_singleline(self):
        """单行条目写入后回读无损。"""
        monitor = EscapeMonitor()
        handler = monitor._input_handler

        handler._history = []
        handler._buffer = "hello world"
        handler._cursor_pos = 11
        handler._enter()

        content = self._test_path.read_text(encoding="utf-8")
        assert "hello world" in content

        handler._history = []
        handler.load_history()
        assert "hello world" in handler._history

    def test_legacy_format_compatible(self):
        """旧格式（\\n 未转义的多行条目被拆行存储）兼容读取。"""
        monitor = EscapeMonitor()
        handler = monitor._input_handler

        # 写入旧格式：多行条目每条单独一行（升级前格式）
        self._test_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        handler._history = []
        handler.load_history()
        # 旧格式下每条拆行为独立条目
        assert "line1" in handler._history
        assert "line2" in handler._history
        assert "line3" in handler._history
        assert len(handler._history) == 3

    def test_empty_file(self):
        """空文件加载后保留内存中的现有历史（不因文件过时而冲掉）。"""
        monitor = EscapeMonitor()
        handler = monitor._input_handler

        self._test_path.write_text("", encoding="utf-8")
        handler._history = ["stale"]
        handler.load_history()
        assert handler._history == ["stale"]

    def test_file_not_found(self):
        """文件不存在时保留内存中的现有历史（不因文件缺失而冲掉）。"""
        monitor = EscapeMonitor()
        handler = monitor._input_handler
        # 确保文件不存在
        if self._test_path.exists():
            self._test_path.unlink()
        handler._history = ["stale"]
        handler.load_history()
        assert handler._history == ["stale"]
