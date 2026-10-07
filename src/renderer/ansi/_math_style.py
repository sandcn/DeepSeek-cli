"""_math_style — 数学公式终端渲染的样式常量与颜色解析（零 Rich）。

从 ``_math_latex`` 拆出的共享样式层：布局原语（``_math_box``）、扩展命令
（``_math_cmds``）、环境渲染（``_math_env``）与解析主体（``_math_latex``）
共用同一套样式，避免各模块各写一份导致颜色漂移。
"""

from __future__ import annotations

from .style import Style

# ── 基础语义色（256 色，暗色终端下对比度良好） ──────────────
_M_SYM = Style(fg=252)
_M_NUM = Style(fg=221)
_M_OP = Style(fg=51, bold=True)
_M_FN = Style(fg=75)
_M_BIGOP = Style(fg=51, bold=True)
_M_BIGOP_LIMIT = Style(fg=45, bold=True)
_M_SUP = Style(fg=51)
_M_SUB = Style(fg=245)
_M_FRAC = Style(fg=213)
_M_TEXT = Style(fg=245, italic=True)
_M_TEXT_ROMAN = Style(fg=248)
_M_TEXT_BOLD = Style(fg=245, italic=True, bold=True)
_M_CODE = Style(fg=46, bold=True)
_M_MATH_BOLD = Style(fg=248, bold=True)
_M_SQRT = Style(fg=213)
_M_ACCENT = Style(fg=51, dim=True)
_M_BOX = Style(fg=226)
_M_CANCEL = Style(fg=203, dim=True)
_M_TAG = Style(fg=240, dim=True)
_M_NOTICE = Style(fg=240, italic=True, dim=True)
_M_FENCE = Style(fg=45, bold=True)
_M_BRACE = Style(fg=214, bold=True)
_M_ARROW = Style(fg=45)
_M_HLINE = Style(fg=240)
_M_COLORBOX_FRAME = Style(fg=226)

#: 颜色名 → 256 色号（``\\color{name}`` / ``\\colorbox`` 等着色命令取值）
_COLOR_256: dict[str, int] = {
    "red": 196, "green": 40, "blue": 33, "yellow": 220,
    "cyan": 44, "magenta": 201, "white": 231, "black": 0,
    "gray": 240, "grey": 240, "darkred": 88, "darkgreen": 22,
    "darkblue": 18, "orange": 214, "purple": 93, "pink": 213,
    "teal": 44, "brown": 130, "navy": 25, "lime": 118,
    "olive": 142, "violet": 177, "gold": 220, "silver": 250,
    "lightgray": 250, "lightgrey": 250, "darkgray": 238,
    "darkgrey": 238, "turquoise": 80, "salmon": 209, "crimson": 161,
    "indigo": 54, "maroon": 88, "aqua": 44, "fuchsia": 201,
    "beige": 230, "tan": 180, "plum": 176, "orchid": 170,
    "khaki": 222, "coral": 209, "azure": 117, "ivory": 230,
}


def color_style(name: str) -> Style:
    """颜色名 / ``#rrggbb`` → 前景样式（未知颜色回退近白）。

    ``\\textcolor{...}`` / ``\\color{...}`` / ``\\colorbox`` 共用——调用方
    只需给出名字，色号映射集中在此（新增颜色只改本表）。
    """
    value = (name or "").strip()
    if value.startswith("#") and len(value) == 7:
        try:
            r = int(value[1:3], 16)
            g = int(value[3:5], 16)
            b = int(value[5:7], 16)
            from .style import rgb_to_256
            return Style(fg=rgb_to_256(r, g, b))
        except ValueError:
            return _M_SYM
    return Style(fg=_COLOR_256.get(value.lower(), 231))


def color_bg(name: str) -> int | None:
    """颜色名 / ``#rrggbb`` → 背景色号（未知颜色返回 ``None``）。"""
    value = (name or "").strip()
    if value.startswith("#") and len(value) == 7:
        try:
            r = int(value[1:3], 16)
            g = int(value[3:5], 16)
            b = int(value[5:7], 16)
            from .style import rgb_to_256
            return rgb_to_256(r, g, b)
        except ValueError:
            return None
    return _COLOR_256.get(value.lower())


__all__ = [
    "_M_SYM", "_M_NUM", "_M_OP", "_M_FN", "_M_BIGOP", "_M_BIGOP_LIMIT",
    "_M_SUP", "_M_SUB", "_M_FRAC", "_M_TEXT", "_M_TEXT_ROMAN", "_M_TEXT_BOLD",
    "_M_CODE", "_M_MATH_BOLD", "_M_SQRT", "_M_ACCENT", "_M_BOX", "_M_CANCEL", "_M_TAG",
    "_M_NOTICE", "_M_FENCE", "_M_BRACE", "_M_ARROW", "_M_HLINE",
    "_M_COLORBOX_FRAME", "_COLOR_256", "color_style", "color_bg",
]
