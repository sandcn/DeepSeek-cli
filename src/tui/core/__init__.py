"""TUI 核心基础工具层（Layer 0）。

精简版 — 仅保留 style.py / color.py / singleton.py / __init__.py。
其他核心模块（animation / effects / text_utils / ansi_utils 等）已随步骤 1 删除。
"""

from __future__ import annotations

from .style import (
    Style, StyledText, StyleSheet,
    FADE_COLOR_DARK, FADE_COLOR_MID,
    SEP_COLOR_START, SEP_COLOR_END,
    ANSI_EMERGENCY_RED, ANSI_EMERGENCY_YELLOW,
    ANSI_EMERGENCY_RESET, ANSI_EMERGENCY_CURSOR_BOTTOM,
)
from .color import TrueColor
from .singleton import SingletonMeta


__all__ = [
    "Style", "StyledText", "StyleSheet",
    "FADE_COLOR_DARK", "FADE_COLOR_MID",
    "SEP_COLOR_START", "SEP_COLOR_END",
    "ANSI_EMERGENCY_RED", "ANSI_EMERGENCY_YELLOW",
    "ANSI_EMERGENCY_RESET", "ANSI_EMERGENCY_CURSOR_BOTTOM",
    "TrueColor",
    "SingletonMeta",
]
