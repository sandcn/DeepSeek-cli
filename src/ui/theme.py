"""
语义化主题颜色映射 — 支持多主题切换。

每个主题预设为 {语义键: ANSI颜色码} 映射。
通过 set_theme(name) 动态切换，THEME 始终保持为当前活动主题的引用。
"""
from __future__ import annotations

from typing import Dict, List
from .ansi import (
    CYAN, DIM, GREEN, YELLOW, RED, DARK_GRAY,
    BLUE, BRIGHT_CYAN, BRIGHT_GREEN,
    BRIGHT_YELLOW, BRIGHT_WHITE, WHITE, GRAY,
)

# ══════════════════════════════════════════════════════════
# 主题预设
# ══════════════════════════════════════════════════════════

THEMES: Dict[str, Dict[str, str]] = {
    # ── 深色主题（默认）────────────────────────────────
    "dark": {
        "title": CYAN,
        "subtitle": DIM,
        "prompt": CYAN,
        "user": CYAN,
        "assistant": GREEN,
        "thinking": DIM,
        "tool": DIM,
        "success": GREEN,
        "warning": YELLOW,
        "error": RED,
        "info": DIM,
        "cost": DIM,
        "separator": DIM,
        "meta": DIM,
        "accent": YELLOW,
        "border": DIM,
        "highlight": CYAN,
        "muted": DARK_GRAY,
        "code": DIM,
        "divider": DIM,
    },

    # ── 亮色主题 ───────────────────────────────────────
    "light": {
        "title": BLUE,
        "subtitle": DIM,
        "prompt": BLUE,
        "user": BLUE,
        "assistant": GREEN,
        "thinking": DIM,
        "tool": DIM,
        "success": GREEN,
        "warning": "\033[33;1m",       # BOLD + YELLOW，亮背景下更醒目
        "error": RED,
        "info": DIM,
        "cost": DIM,
        "separator": DIM,
        "meta": DIM,
        "accent": "\033[33;1m",
        "border": DIM,
        "highlight": BLUE,
        "muted": GRAY,
        "code": DIM,
        "divider": DIM,
    },

    # ── 高对比主题 ─────────────────────────────────────
    "high-contrast": {
        "title": BRIGHT_CYAN,
        "subtitle": DIM,
        "prompt": BRIGHT_CYAN,
        "user": BRIGHT_CYAN,
        "assistant": BRIGHT_GREEN,
        "thinking": WHITE,
        "tool": WHITE,
        "success": BRIGHT_GREEN,
        "warning": BRIGHT_YELLOW,
        "error": "\033[31;1m",         # BOLD + RED
        "info": BRIGHT_WHITE,
        "cost": BRIGHT_WHITE,
        "separator": DIM,
        "meta": BRIGHT_WHITE,
        "accent": BRIGHT_YELLOW,
        "border": BRIGHT_WHITE,
        "highlight": BRIGHT_CYAN,
        "muted": GRAY,
        "code": BRIGHT_WHITE,
        "divider": DIM,
    },
}

# ══════════════════════════════════════════════════════════
# 当前活动主题
# ══════════════════════════════════════════════════════════

_ACTIVE_NAME: str = "dark"
THEME: Dict[str, str] = dict(THEMES["dark"])  # 可变的当前主题副本


def set_theme(name: str) -> None:
    """切换到指定主题。

    Args:
        name: 主题名称（"dark" / "light" / "high-contrast"）

    Raises:
        ValueError: 主题名称不存在
    """
    if name not in THEMES:
        raise ValueError(f"未知主题: {name}，可用主题: {', '.join(THEMES.keys())}")
    global _ACTIVE_NAME, THEME
    _ACTIVE_NAME = name
    THEME.clear()
    THEME.update(THEMES[name])


def get_active_theme() -> str:
    """返回当前主题名称。"""
    return _ACTIVE_NAME


def list_themes() -> List[str]:
    """返回所有可用主题名称列表。"""
    return list(THEMES.keys())


def get_theme_names_with_desc() -> List[tuple[str, str]]:
    """返回 (主题名, 简短描述) 列表。"""
    return [
        ("dark", "深色主题（默认）"),
        ("light", "亮色主题（浅色背景用）"),
        ("high-contrast", "高对比主题（高可读性）"),
    ]


__all__ = [
    "THEME", "THEMES",
    "set_theme", "get_active_theme", "list_themes",
    "get_theme_names_with_desc",
]
