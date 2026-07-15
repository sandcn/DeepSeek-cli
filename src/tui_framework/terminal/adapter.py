"""
TerminalAdapter — 终端 I/O 抽象层（框架精简版）

封装终端写入、尺寸获取等基础操作。
职责单一：仅处理「如何输出到终端」，不关心输出什么内容。
不包含全屏帧渲染（render_frame/DECSTBM/SCOSC/DECRC），
这些功能由调用方在业务层实现。

可替换性：
- 实现相同接口即可替换为标准输出、日志文件、WebSocket 等目标
- 测试时可注入 MockTerminalAdapter 验证输出行为
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from .blessed import get_terminal

_logger = logging.getLogger(__name__)


def query_terminal_size() -> tuple[int, int]:
    """通过 Blessed Terminal 获取终端尺寸。

    Blessed 内部使用 ioctl(TIOCGWINSZ) 查询终端尺寸，
    比 shutil.get_terminal_size() 更可靠（不依赖环境变量回落）。

    Returns:
        (columns, rows) 元组。
    """
    try:
        term = get_terminal()
        return term.width, term.height
    except Exception:
        return 80, 24


class TerminalAdapter:
    """终端 I/O 抽象层（框架精简版）。

    提供终端写入、尺寸获取等基础操作。
    不包含全屏帧渲染（render_frame），由调用方在业务层实现。
    不依赖全局锁或外部状态，线程安全由调用方保证。
    """

    def __init__(self, stdout: Optional = None):
        self._stdout = stdout or sys.stdout

    # ── 终端尺寸 ────────────────────────────────────────

    @staticmethod
    def _query_terminal_size() -> tuple[int, int]:
        """委托给模块级 query_terminal_size()。"""
        return query_terminal_size()

    @property
    def terminal_width(self) -> int:
        """获取终端宽度（列数）。"""
        return self._query_terminal_size()[0]

    @property
    def terminal_height(self) -> int:
        """获取终端高度（行数）。"""
        return self._query_terminal_size()[1]

    def get_terminal_size(self) -> tuple[int, int]:
        """获取终端尺寸 (列数, 行数)。"""
        return self._query_terminal_size()

    # ── 基础 I/O ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入文本到终端（含 flush）。"""
        self._stdout.write(text)
        self._stdout.flush()

    def write_raw(self, text: str) -> None:
        """写入原始文本到终端（不含 flush）。"""
        self._stdout.write(text)

    def flush(self) -> None:
        """强制刷新终端输出缓冲区。"""
        self._stdout.flush()

    def write_line(self, text: str = "") -> None:
        """写入一行文本（追加换行符）。"""
        self._stdout.write(text + "\n")
        self._stdout.flush()

    # ── ANSI 控制 ───────────────────────────────────────

    @staticmethod
    def set_window_title(title: str) -> None:
        """设置终端窗口标题（OSC 0 escape sequence: \\033]0;title\\007）。"""
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()


__all__ = [
    "TerminalAdapter",
    "query_terminal_size",
]
