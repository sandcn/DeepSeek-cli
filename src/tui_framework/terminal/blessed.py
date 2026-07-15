"""Blessed Terminal 单例管理 — 全局共享 Blessed Terminal 实例。

Blessed 的 Terminal 实例封装了终端类型检测、能力查询、颜色支持等。
整个应用应共享同一 Terminal 实例，避免重复开销。

使用方式：
    from tui_framework.terminal.blessed import get_terminal
    term = get_terminal()
    term.move_xy(0, 0)
    term.clear_eol()

设计决策：
  - 惰性初始化：首次调用 get_terminal() 时才创建 Terminal 实例
  - 测试可重置：reset_terminal() 允许测试环境重建实例
  - stream 参数保持默认（sys.__stdout__），但调用方仍使用自己的
    sys.__stdout__ 写入（经过 _StdoutLineTracker 包装）。
    Blessed 仅用于生成序列字符串（move_xy, clear_eol 等）。
  - 零开销：get_terminal() 返回 cached 实例，O(1) 复杂度。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from blessed import Terminal

_logger = logging.getLogger(__name__)

_terminal: Terminal | None = None


def get_terminal() -> Terminal:
    """获取全局共享的 Blessed Terminal 实例（惰性创建）。

    Returns:
        Terminal 实例（首次调用时创建，后续返回缓存实例）。

    Raises:
        ImportError: blessed 库未安装。
    """
    global _terminal
    if _terminal is None:
        from blessed import Terminal as _Terminal
        _terminal = _Terminal()
        _logger.debug("Blessed Terminal 实例已创建: %s", _terminal)
    return _terminal


def reset_terminal() -> None:
    """重置 Terminal 实例（测试用）。

    调用后下次 get_terminal() 会重新创建实例。
    仅在测试环境中使用，生产代码不应调用。
    """
    global _terminal
    _terminal = None


def blessed_available() -> bool:
    """检查 Blessed 是否可用（不触发实例创建）。

    Returns:
        True 表示 blessed 已安装，False 表示不可用。
    """
    try:
        import blessed  # noqa: F401
        return True
    except ImportError:
        return False


__all__ = [
    "get_terminal",
    "reset_terminal",
    "blessed_available",
]
