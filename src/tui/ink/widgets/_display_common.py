"""展示控件公共辅助 — 颜色/样式解析 / 宽度重复填充。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——展示控件
（Spinner/ProgressBar/Table/Badge/Divider/Panel）共享的纯辅助独立成模块，
控件实现分别拆至 ``_spinner`` / ``_progress`` / ``_table`` /
``_badge_divider`` / ``_panel``，``display.py`` 门面 re-export。

依赖方向：本模块 → element/output/core.style/_width；不反向依赖控件模块。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_color 原本地定义（与
#   _interactive_common 逐字重复）——收敛至 _widget_common 单一真源。
from ._widget_common import _color


def _resolve_style(props: dict, default_fg: int | None = None) -> Style | None:
    """合并 ``color``（fg shorthand）+ ``style`` 为 Style。

    ``color`` 解析成功后覆盖 style.fg；``style`` 为 None 时仅 color 生效。
    """
    color = props.get("color")
    style = props.get("style")
    fg = _color(color, default_fg) if color is not None else None
    if style is None and fg is None:
        return None
    merged = Style(fg=fg) if fg is not None else None
    if style is not None:
        merged = style.merge(merged) if merged else style
    return merged


def _repeat_to_width(char: str, width: int) -> str:
    """以 char 重复填充至目标显示宽度（不足部分补空格；宽字符按宽度换算）。"""
    if width <= 0:
        return ""
    cw = wcswidth_simple(char)
    if cw <= 0:
        # ★ P3（review）：零宽字符无法以重复填充达宽（修复前 ``max(1, 0)=1``
        #   将零宽字符按 1 计，count=width 但实际宽度 0，永远达不成目标宽）——
        #   零宽字符回退纯空格填充。
        return " " * width
    count = width // cw
    out = char * count
    remain = width - cw * count
    if remain:
        out += " " * remain
    return out


__all__ = ["_color", "_resolve_style", "_repeat_to_width"]
