"""ANSI 颜色常量与工具函数 — 向后兼容存根（从 src.tui.core.ansi_utils 重新导出）

变更说明：ANSI 工具已迁移到 src/tui/core/ansi_utils.py，颜色常量保留在 src/core/constants.py。
          此文件保留为向后兼容存根。
"""
from __future__ import annotations

from ..core.constants import (
    GRAY, WHITE, CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA,
    BOLD, DIM, RESET, ITALIC, UNDERLINE,
    BRIGHT_CYAN, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE,
    BRIGHT_MAGENTA, BRIGHT_RED, BRIGHT_WHITE, BRIGHT_BLACK,
    BG_BLUE, BG_CYAN, BG_GREEN, BG_YELLOW,
    ORANGE, TEAL, PINK, LAVENDER,
    SOFT_GREEN, SOFT_BLUE, SOFT_YELLOW, DARK_GRAY,
)
from ..tui.core.ansi_utils import (
    _ANSI_CLEAN_RE, _ANSI_SEQ_RE,
    strip_ansi, visual_width, truncate_ansi_visual,
    skip_ansi_sgr, truncate_ansi_sgr,
    truncate_ansi_line,
)

__all__ = [
    "GRAY", "WHITE", "CYAN", "GREEN", "YELLOW", "RED", "BLUE", "MAGENTA",
    "BOLD", "DIM", "RESET", "ITALIC", "UNDERLINE",
    "BRIGHT_CYAN", "BRIGHT_GREEN", "BRIGHT_YELLOW", "BRIGHT_BLUE",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_BLACK",
    "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_YELLOW",
    "ORANGE", "TEAL", "PINK", "LAVENDER",
    "SOFT_GREEN", "SOFT_BLUE", "SOFT_YELLOW", "DARK_GRAY",
    "strip_ansi", "visual_width", "truncate_ansi_visual",
    "skip_ansi_sgr", "truncate_ansi_sgr",
    "truncate_ansi_line",
    "_ANSI_CLEAN_RE", "_ANSI_SEQ_RE",
]
