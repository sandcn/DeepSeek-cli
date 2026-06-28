"""
终端颜色和样式管理（兼容性外观层）

已拆分为:
  - core/constants.py:  ANSI 颜色常量（权威源，core 层可安全导入）
  - ansi.py:            ANSI 工具函数（视觉宽度/截断/清洗）
  - theme.py:           语义化主题颜色映射
  - console.py:         rich.Console 惰性初始化

兼容性：本模块重新导出 core.constants 中的颜色常量 + ansi.py 中的工具函数，
外部代码可继续使用 from ..ui.colors import GREEN, YELLOW, ...
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
from .ansi import (
    strip_ansi, visual_width, truncate_ansi_visual,
    skip_ansi_sgr, truncate_ansi_sgr,
)
from .theme import THEME
from .console import get_console as _get_console

# 向后兼容：colors.console 可正常访问
console = _get_console()

__all__: list[str] = ["THEME", "console"]
