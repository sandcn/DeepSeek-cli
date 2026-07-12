"""
语义化主题颜色映射 — 支持多主题切换。

每个主题预设为 {语义键: ANSI颜色码} 映射。
通过 set_theme(name) 动态切换，THEME 始终保持为当前活动主题的引用。

所有颜色值已升级为 256 色 ANSI 码（格式 \033[38;5;Nm 或 \033[48;5;Nm），
保持与 src/core/constants.py 中 _256 后缀常量一致。
"""
from __future__ import annotations

from typing import Dict, List

# ══════════════════════════════════════════════════════════
# 主题预设
# ══════════════════════════════════════════════════════════

THEMES: Dict[str, Dict[str, str]] = {
    # ── 深色主题（默认）────────────────────────────────
    "dark": {
        "title": "\033[38;5;45m",          # 青色
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;45m",         # 青色
        "user": "\033[38;5;45m",           # 青色
        "assistant": "\033[38;5;41m",      # 绿色
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;41m",        # 绿色
        "warning": "\033[38;5;221m",       # 琥珀黄
        "error": "\033[38;5;196m",         # 红色
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 暗灰
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[38;5;221m",        # 琥珀黄
        "border": "\033[38;5;239m",        # 暗灰
        "highlight": "\033[38;5;45m",      # 青色
        "muted": "\033[38;5;237m",         # 深灰
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 暗灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;41m",   # 绿色
        "progress_empty": "\033[38;5;236m",   # 深灰
        "diff_add": "\033[38;5;41m",          # 绿色
        "diff_del": "\033[38;5;196m",         # 红色
        "diff_ctx": "\033[38;5;242m",         # 中灰
        "border_active": "\033[38;5;45m",     # 青色
        "border_inactive": "\033[38;5;237m",  # 深灰
        "overlay_bg": "\033[48;5;235m",       # 暗色背景
        "tag_code": "\033[38;5;221m",         # 琥珀黄
    },

    # ── 亮色主题（浅色背景用）──────────────────────────
    "light": {
        "title": "\033[38;5;33m",          # 蓝色
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;33m",         # 蓝色
        "user": "\033[38;5;33m",           # 蓝色
        "assistant": "\033[38;5;41m",      # 绿色
        "thinking": "\033[38;5;242m",      # 中灰
        "tool": "\033[38;5;242m",          # 中灰
        "success": "\033[38;5;41m",        # 绿色
        "warning": "\033[1;38;5;221m",     # BOLD + 琥珀黄
        "error": "\033[38;5;196m",         # 红色
        "info": "\033[38;5;242m",          # 中灰
        "cost": "\033[38;5;242m",          # 中灰
        "separator": "\033[38;5;239m",     # 暗灰
        "meta": "\033[38;5;242m",          # 中灰
        "accent": "\033[1;38;5;221m",      # BOLD + 琥珀黄
        "border": "\033[38;5;239m",        # 暗灰
        "highlight": "\033[38;5;33m",      # 蓝色
        "muted": "\033[38;5;242m",         # 中灰
        "code": "\033[38;5;242m",          # 中灰
        "divider": "\033[38;5;239m",       # 暗灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;41m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;41m",
        "diff_del": "\033[38;5;196m",
        "diff_ctx": "\033[38;5;242m",
        "border_active": "\033[38;5;33m",
        "border_inactive": "\033[38;5;237m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;221m",
    },

    # ── 高对比主题（高可读性）──────────────────────────
    "high-contrast": {
        "title": "\033[38;5;81m",          # 亮青
        "subtitle": "\033[38;5;242m",      # 中灰
        "prompt": "\033[38;5;81m",         # 亮青
        "user": "\033[38;5;81m",           # 亮青
        "assistant": "\033[38;5;47m",      # 亮绿
        "thinking": "\033[38;5;15m",       # 白
        "tool": "\033[38;5;15m",           # 白
        "success": "\033[38;5;47m",        # 亮绿
        "warning": "\033[38;5;227m",       # 亮黄
        "error": "\033[1;38;5;196m",       # BOLD + 红
        "info": "\033[38;5;255m",          # 亮白
        "cost": "\033[38;5;255m",          # 亮白
        "separator": "\033[38;5;242m",     # 中灰
        "meta": "\033[38;5;255m",          # 亮白
        "accent": "\033[38;5;227m",        # 亮黄
        "border": "\033[38;5;255m",        # 亮白
        "highlight": "\033[38;5;81m",      # 亮青
        "muted": "\033[38;5;250m",         # 浅灰
        "code": "\033[38;5;255m",          # 亮白
        "divider": "\033[38;5;242m",       # 中灰
        # ── 新增语义键（步骤 3） ──
        "progress_filled": "\033[38;5;47m",
        "progress_empty": "\033[38;5;236m",
        "diff_add": "\033[38;5;47m",
        "diff_del": "\033[1;38;5;196m",
        "diff_ctx": "\033[38;5;255m",
        "border_active": "\033[38;5;81m",
        "border_inactive": "\033[38;5;242m",
        "overlay_bg": "\033[48;5;235m",
        "tag_code": "\033[38;5;227m",
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
