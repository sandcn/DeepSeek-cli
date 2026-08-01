"""测试 src/core/_terminal.py — 共享终端宽度查询。

覆盖三路径：ioctl(/dev/tty) 优先、ioctl 失败回退 shutil、全部失败返回 80。

说明：_terminal.get_terminal_width 内部延迟 import os/fcntl/shutil
（函数级 import，保持原 parallel_executor 实现风格），因此测试使用
全局 patch（os.open / fcntl.ioctl / shutil.get_terminal_size），
与函数内 import 获取同一模块对象。
"""

from __future__ import annotations

import os
import struct
from unittest.mock import patch

from src.core._terminal import get_terminal_width


class TestTerminalWidth:
    """共享终端宽度查询三路径。"""

    def test_ioctl_path_regression(self) -> None:
        """ioctl 成功时返回真实列数（mock fcntl.ioctl 返回 (24,100)）。"""
        with patch("os.open", return_value=3) as mock_open, \
             patch("fcntl.ioctl", return_value=struct.pack("HHHH", 24, 100, 0, 0)) as mock_ioctl, \
             patch("os.close") as mock_close:
            assert get_terminal_width() == 100
        mock_open.assert_called_once()
        mock_ioctl.assert_called_once()
        mock_close.assert_called_once()

    def test_fallback_shutil_regression(self) -> None:
        """ioctl 抛异常时回退 shutil.get_terminal_size()。"""
        with patch("os.open", side_effect=OSError("no tty")), \
             patch("shutil.get_terminal_size",
                   return_value=os.terminal_size((100, 24))):
            assert get_terminal_width() == 100

    def test_fallback_80_regression(self) -> None:
        """ioctl 与 shutil 全部失败时返回 80 兜底。"""
        with patch("os.open", side_effect=OSError("no tty")), \
             patch("shutil.get_terminal_size", side_effect=OSError("no size")):
            assert get_terminal_width() == 80
