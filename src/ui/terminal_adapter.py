"""终端 I/O 抽象层 — 向后兼容存根（从 src.tui.terminal.adapter 重新导出）

变更说明：TerminalAdapter 已迁移到 src/tui/terminal/adapter.py，此文件保留为向后兼容存根。
"""
from __future__ import annotations

from ..tui.terminal.adapter import (
    TerminalAdapter,
    query_terminal_size,
    register_sigwinch_callback,
    unregister_sigwinch_callback,
)

__all__ = [
    "TerminalAdapter",
    "query_terminal_size",
    "register_sigwinch_callback",
    "unregister_sigwinch_callback",
]
