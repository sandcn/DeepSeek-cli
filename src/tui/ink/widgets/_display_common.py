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
from ..helpers import strip_ansi


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


def _truncate_to_width(text: str, max_w: int, strip_ansi_seq: bool = False) -> str:
    """按显示宽度截断（不拆 CJK；超宽时末尾补省略号）。

    ★ P2（review 2026-08-22）：从 codeblock / _badge_divider 重复实现收敛——
    ``strip_ansi_seq`` 参数化两处差异：codeblock 截断前剥离 ANSI 转义（避免
    截断切在序列中间/宽度统计错误），badge_divider 标题保留样式不剥离。
    返回宽度 <= max_w（保留 ``max_w-1`` 字符宽 + 省略号 1 宽）。
    """
    if max_w <= 0:
        return ""
    if strip_ansi_seq:
        text = strip_ansi(text)
    if wcswidth_simple(text) <= max_w:
        return text
    w = 0
    out = []
    for ch in text:
        cw = wcswidth_simple(ch)
        if w + cw > max_w - 1:
            break
        out.append(ch)
        w += cw
    return "".join(out) + "\u2026"


__all__ = ["_color", "_resolve_style", "_repeat_to_width", "_truncate_to_width"]
