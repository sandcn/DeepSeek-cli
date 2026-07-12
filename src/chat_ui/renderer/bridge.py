"""Rich-TUI 动效桥接模块。

将 _effects.py 的 256 色呼吸/闪烁动效桥接到 Rich 颜色管线，
使 Rich 组件（ThinkingBlock/AnswerBlock/ErrorBlock 等）获得呼吸色效果。

核心函数：
  - breath_color(low, high, period): 返回当前帧 Rich 兼容呼吸色
  - animated_style(base_style, color_func): 动态叠加动效色到 Style
"""

from __future__ import annotations

from rich.color import Color as RichColor
from rich.style import Style as RichStyle
from rich.text import Text as RichText

from ...ui.tui._animator import AnimatorContext
from ...ui.tui._effects import sine_color as _sine_color, sparkle_color as _sparkle_color


# ── 256 色号 → Rich Color 名称映射 ───────────────────────
# Rich 内置支持 256 色名称，格式为 "color(<N>)" 如 "color(45)"
# 也可直接用 "color(45)" 字符串创建 Rich Color
_RICH_256_FMT = "color({})"


def _to_rich_color(color_num: int) -> str:
    """将 256 色号转为 Rich 兼容颜色字符串。

    Args:
        color_num: 256 色号（0-255）。

    Returns:
        Rich 颜色名称，如 "color(45)"。
    """
    return _RICH_256_FMT.format(color_num)


def get_breath_color(
    color_low: int = 45, color_high: int = 81,
    period: int = 12,
) -> str:
    """获取当前帧 256 色呼吸色号，转为 Rich 颜色名。

    使用 AnimatorContext 的全局帧号计算正弦波呼吸色。

    Args:
        color_low: 最暗色号。
        color_high: 最亮色号。
        period: 呼吸周期帧数。

    Returns:
        Rich 颜色名，如 "color(45)"。
    """
    frame = AnimatorContext.get_default().frame
    c = _sine_color(frame, color_low, color_high, period)
    return _to_rich_color(c)


def get_sparkle_color(
    base_color: int = 45, period: int = 6,
) -> str:
    """获取当前帧闪烁色，转为 Rich 颜色名。

    Args:
        base_color: 基准色号。
        period: 闪烁周期。

    Returns:
        Rich 颜色名。
    """
    frame = AnimatorContext.get_default().frame
    c = _sparkle_color(frame, base_color, period=period)
    return _to_rich_color(c)


def get_morph_color(
    palette_a: list[int],
    palette_b: list[int],
    morph_period: int = 60,
    breath_period: int = 12,
) -> str:
    """获取当前帧 morph_color 过渡色，转为 Rich 颜色名。

    在 palette_a 和 palette_b 之间随时间平滑过渡（morph），
    同时叠加正弦波呼吸。适合"情绪渐变"场景（如思考→回答过渡）。

    Args:
        palette_a: 调色板 A（起点，如蓝紫系 [24,33,42]）。
        palette_b: 调色板 B（终点，如青绿系 [41,82,122]）。
        morph_period: 变形周期帧数。
        breath_period: 呼吸周期帧数。

    Returns:
        Rich 颜色名，如 "color(45)"。
    """
    from ...ui.tui._effects import morph_color
    frame = AnimatorContext.get_default().frame
    c = morph_color(frame, palette_a, palette_b, morph_period, breath_period)
    return _to_rich_color(c)


def make_morph_style(
    palette_a: list[int],
    palette_b: list[int],
    morph_period: int = 60,
    breath_period: int = 12,
    bold: bool = False,
) -> RichStyle:
    """创建 morph_color 过渡色 Rich Style。

    用于思考→回答过渡等情绪渐变场景，在 palette_a 和 palette_b
    之间随时间平滑过渡，同时叠加正弦波呼吸。

    Args:
        palette_a: 调色板 A（起点，如蓝紫系）。
        palette_b: 调色板 B（终点，如青绿系）。
        morph_period: 变形周期帧数。
        breath_period: 呼吸周期帧数。
        bold: 是否加粗。

    Returns:
        带当前帧过渡色的 Rich Style。
    """
    c = get_morph_color(palette_a, palette_b, morph_period, breath_period)
    return RichStyle(color=c, bold=bold)


def make_breath_style(
    color_low: int = 45, color_high: int = 81,
    period: int = 12, bold: bool = False,
) -> RichStyle:
    """创建呼吸色 Rich Style。

    Args:
        color_low: 最暗色号。
        color_high: 最亮色号。
        period: 呼吸周期。
        bold: 是否加粗。

    Returns:
        带当前帧呼吸色的 Rich Style。
    """
    c = get_breath_color(color_low, color_high, period)
    return RichStyle(color=c, bold=bold)


def assemble_with_breath(
    parts: list[tuple[str, str | RichStyle]],
) -> RichText:
    """组装带呼吸色的 Rich Text。

    parts 中的颜色可以是 Rich 颜色名（如 "color(45)"）
    或 RichStyle 对象。

    Args:
        parts: [(text, style), ...] 样式列表。

    Returns:
        RichText 对象。
    """
    text = RichText()
    for content, style in parts:
        if isinstance(style, str):
            text.append(content, style=RichStyle(color=style))
        else:
            text.append(content, style=style)
    return text


__all__ = [
    "get_breath_color",
    "get_sparkle_color",
    "get_morph_color",
    "make_morph_style",
    "make_breath_style",
    "assemble_with_breath",
    "_to_rich_color",
]
