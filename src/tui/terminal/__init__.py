"""TUI 终端 I/O 层 — 终端写入、宽度检测、窄屏自适应、Blessed 单例

统一管理：
  1. 终端宽度检测（TTL 缓存，减少 syscall）— 使用 Blessed Terminal
  2. output_lock 保护的终端写入上下文管理器 LockedTerminal
  3. 窄屏自适应函数（is_narrow, narrow_truncate 等）
  4. Blessed Terminal 单例管理
  5. 端口接口定义（ILockedTerminal）
  6. Raw Mode 保护

分层引用约定：
  - 本层依赖：src.tui.core（TTLCache）
  - 上层依赖本层：pipeline / widgets / consumer / parallel / frame
"""

from __future__ import annotations

from .terminal import (
    ILockedTerminal,
    LockedTerminal,
    get_terminal_width,
    is_narrow,
    narrow_truncate,
    narrow_indent,
    narrow_sep_width,
    NARROW_THRESHOLD,
    EXTRA_NARROW_THRESHOLD,
    enter_raw_mode,
    leave_raw_mode,
)
from .ports import ILockedTerminal as _ILockedTerminalFromPorts  # noqa: F401
from .blessed import get_terminal as _get_terminal  # noqa: F401
from .narrow import (
    is_narrow as _is_narrow,
    narrow_truncate as _narrow_truncate,
)

__all__ = [
    "ILockedTerminal",
    "LockedTerminal",
    "get_terminal_width",
    "is_narrow",
    "narrow_truncate",
    "narrow_indent",
    "narrow_sep_width",
    "NARROW_THRESHOLD",
    "EXTRA_NARROW_THRESHOLD",
    "enter_raw_mode",
    "leave_raw_mode",
]
