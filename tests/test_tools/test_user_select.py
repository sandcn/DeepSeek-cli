"""user_select 重构后的单元测试。

验证：
  - _execute_terminal_async 不再直接调用 os.read/termios
  - termios 操作通过 EscapeMonitor 公开方法
  - stdin 排空通过 Input.flush_stdin_buffer()
  - Input 的 read_byte/read_with_timeout 被正确使用
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ── TestUserSelectNoDirectTermios ──────────────────────────────────

class TestUserSelectNoDirectTermios:
    """验证 user_select 不再直接操作 termios / os.read。"""

    @pytest.fixture
    def mock_monitor(self):
        """创建 mock EscapeMonitor。"""
        m = MagicMock()
        m.apply_monitor_settings = MagicMock()
        m.restore_terminal_settings = MagicMock()
        m.stop = MagicMock()
        m.start = MagicMock()
        return m

    @pytest.fixture
    def mock_input(self):
        """创建 mock Input 实例。"""
        m = MagicMock()
        m.fd = 0  # /dev/null fd
        m.flush_stdin_buffer = MagicMock()
        m.read_byte = MagicMock(return_value=b'\r')  # Enter
        m.read_with_timeout = MagicMock(return_value=None)
        return m

    @pytest.fixture
    def mock_chat_ui(self, mock_input):
        """创建 mock ChatUI。"""
        ui = MagicMock()
        ui._components = MagicMock()
        ui._components.input = mock_input
        bb = MagicMock()
        bb._active = True
        ui.bottom_bar = bb
        return ui

    def test_methods_deleted(self):
        """验证 _flush_stdin / _save_termios / _restore_termios 已删除。"""
        from src.tools.user_select import UserSelectFunc
        assert not hasattr(UserSelectFunc, '_flush_stdin'), \
            "_flush_stdin 应已删除"
        assert not hasattr(UserSelectFunc, '_save_termios'), \
            "_save_termios 应已删除"
        assert not hasattr(UserSelectFunc, '_restore_termios'), \
            "_restore_termios 应已删除"

    def test_no_os_read_in_source(self):
        """验证源码中 os.read 调用仅在 fallback 路径。"""
        import inspect
        from src.tools import user_select as us_mod
        source = inspect.getsource(us_mod.UserSelectFunc._execute_terminal_async)
        # os.read 应仅在 fallback 子句中（input_ is None）
        assert 'input_.read_byte()' in source
        assert 'input_.read_with_timeout' in source
        # 不应存在直接 os.read(fd, 1)（不含 fallback）
        lines = source.split('\n')
        for line in lines:
            if 'os.read' in line and 'input_' not in line:
                pytest.fail(f"发现直接 os.read 调用（非 fallback）：{line.strip()}")

    @pytest.mark.asyncio
    async def test_uses_input_flush_stdin_buffer(self, mock_monitor, mock_input, mock_chat_ui):
        """验证 _execute_terminal_async 使用 Input.flush_stdin_buffer()。"""
        from src.tools.user_select import UserSelectFunc

        us = UserSelectFunc("test", ["a", "b"])

        with patch("src.tools.user_select.get_active_monitor", return_value=mock_monitor), \
             patch("src.tools.user_select.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            # 正常情况下 read_byte 返回 Enter 快速退出
            await us._execute_terminal_async()

        mock_input.flush_stdin_buffer.assert_called()

    @pytest.mark.asyncio
    async def test_uses_monitor_apply_settings(self, mock_monitor, mock_input, mock_chat_ui):
        """验证使用 EscapeMonitor.apply_monitor_settings()。"""
        from src.tools.user_select import UserSelectFunc

        us = UserSelectFunc("test", ["a", "b"])

        with patch("src.tools.user_select.get_active_monitor", return_value=mock_monitor), \
             patch("src.tools.user_select.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            await us._execute_terminal_async()

        mock_monitor.apply_monitor_settings.assert_called()

    @pytest.mark.asyncio
    async def test_uses_monitor_restore_in_finally(self, mock_monitor, mock_input, mock_chat_ui):
        """验证 finally 块使用 EscapeMonitor.restore_terminal_settings()。"""
        from src.tools.user_select import UserSelectFunc

        us = UserSelectFunc("test", ["a", "b"])

        with patch("src.tools.user_select.get_active_monitor", return_value=mock_monitor), \
             patch("src.tools.user_select.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            await us._execute_terminal_async()

        mock_monitor.restore_terminal_settings.assert_called()

    @pytest.mark.asyncio
    async def test_uses_input_read_byte(self, mock_monitor, mock_input, mock_chat_ui):
        """验证使用 Input.read_byte() 读取按键。"""
        from src.tools.user_select import UserSelectFunc

        # 设置 read_byte 返回 Enter 键快速完成
        mock_input.read_byte = MagicMock(return_value=b'\r')

        us = UserSelectFunc("test", ["a", "b"])

        with patch("src.tools.user_select.get_active_monitor", return_value=mock_monitor), \
             patch("src.tools.user_select.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            await us._execute_terminal_async()

        mock_input.read_byte.assert_called()

    @pytest.mark.asyncio
    async def test_stops_and_starts_monitor(self, mock_monitor, mock_input, mock_chat_ui):
        """验证停止并重新启动 EscapeMonitor。"""
        from src.tools.user_select import UserSelectFunc

        mock_input.read_byte = MagicMock(return_value=b'\r')

        us = UserSelectFunc("test", ["a", "b"])

        with patch("src.tools.user_select.get_active_monitor", return_value=mock_monitor), \
             patch("src.tools.user_select.get_active_chat_ui", return_value=mock_chat_ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            await us._execute_terminal_async()

        mock_monitor.stop.assert_called()
        mock_monitor.start.assert_called()
