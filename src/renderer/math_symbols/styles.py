"""数学渲染样式常量。"""

from __future__ import annotations

from typing import Dict
from rich.style import Style


_STYLE_DEFAULT = Style()
_STYLE_FUNCTION = Style(color="blue", italic=False)
_STYLE_NUMBER = Style(color="yellow")
_STYLE_OPERATOR = Style(color="bright_cyan")
_STYLE_SUPERSCRIPT = Style(color="bright_cyan")
_STYLE_SUBSCRIPT = Style(dim=True)
_STYLE_FRAC_LINE = Style(color="bright_magenta")
_STYLE_TEXT = Style(italic=True, dim=True)
_STYLE_BOLD = Style(bold=True)
_STYLE_ITALIC = Style(italic=True)
_STYLE_INLINE = Style(color="cyan", italic=True)
_STYLE_BLOCK = Style(color="bright_magenta", bold=True)
_STYLE_CANCEL = Style(strike=True, color="red")
_STYLE_TAG = Style(dim=True, color="bright_black")
_STYLE_BOXED = Style(color="bright_yellow")
_STYLE_COLOR_NOTICE = Style(dim=True, italic=True)
_STYLE_ACCENT = Style(color="bright_cyan", dim=True)

# 简易颜色名 → Rich 颜色名
_COLOR_ALIAS: Dict[str, str] = {
    "red": "red", "green": "green", "blue": "blue",
    "yellow": "yellow", "cyan": "cyan", "magenta": "magenta",
    "white": "white", "black": "black",
    "gray": "grey", "grey": "grey",
    "darkred": "dark_red", "darkgreen": "dark_green",
    "darkblue": "dark_blue", "darkcyan": "dark_cyan",
    "darkmagenta": "dark_magenta",
    "lightgray": "light_gray", "lightgrey": "light_grey",
    "orange": "orange", "purple": "magenta",
    "teal": "cyan", "pink": "bright_magenta",
    "brown": "bright_black", "navy": "blue",
}
