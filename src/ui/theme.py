"""语义化主题颜色映射 — 向后兼容存根（从 src.tui.core.theme 重新导出）

变更说明：主题系统已迁移到 src/tui/core/theme.py，此文件保留为向后兼容存根。
"""
from __future__ import annotations

from ..tui.core.theme import THEME, THEMES, set_theme, get_active_theme, list_themes, get_theme_names_with_desc

__all__ = ["THEME", "THEMES", "set_theme", "get_active_theme", "list_themes", "get_theme_names_with_desc"]
