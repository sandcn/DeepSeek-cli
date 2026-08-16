"""Badge / Divider — 徽章与分隔线控件（React Ink 风格）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——徽章与
分隔线独立成模块（公共辅助经 ``_display_common`` 共享）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from ..element import TEXT, Element, h
from ..widgets.layout import Row
from ._display_common import _color, _resolve_style, _repeat_to_width


# ═══════════════════════════════════════════════════════════
# Badge — 背景色块徽章
# ═══════════════════════════════════════════════════════════

#: Badge 前景自动对比色（bg 偏暗 → 亮前景；bg 偏亮 → 暗前景）
_BADGE_FG_DARK = 231
_BADGE_FG_LIGHT = 232

#: 基础 16 色近似亮度（0-255 标度；用于前景对比判断）
_BASE_BRIGHTNESS: dict[int, int] = {
    0: 0,      # black
    1: 139,    # red
    2: 146,    # green
    3: 178,    # yellow
    4: 93,     # blue
    5: 158,    # magenta
    6: 170,    # cyan
    7: 192,    # white
    8: 128,    # brightBlack/gray
    9: 255,    # brightRed
    10: 255,   # brightGreen
    11: 255,   # brightYellow
    12: 255,   # brightBlue
    13: 255,   # brightMagenta
    14: 255,   # brightCyan
    15: 255,   # brightWhite
}
_ANSI_LEVELS = (0, 95, 135, 175, 215, 255)
#: 亮度阈值：背景亮度超过该值视为亮背景（用暗前景）
_BADGE_BRIGHTNESS_THRESHOLD = 150


def _ansi256_brightness(color: int) -> float:
    """估算 256 色号近似亮度（0-255 标度，NTSC 加权）。"""
    if color < 16:
        return _BASE_BRIGHTNESS.get(color, 128)
    if color >= 232:
        return (color - 232) * 10 + 8  # 灰阶 232→8、255→238 线性
    n = color - 16
    r = _ANSI_LEVELS[n // 36]
    g = _ANSI_LEVELS[(n // 6) % 6]
    b = _ANSI_LEVELS[n % 6]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _badge_fg_for_bg(bg: int) -> int:
    """根据背景色近似亮度返回可读前景色号（亮背景→暗前景，反之亦然）。"""
    if _ansi256_brightness(bg) > _BADGE_BRIGHTNESS_THRESHOLD:
        return _BADGE_FG_LIGHT  # 亮背景 → 暗前景
    return _BADGE_FG_DARK  # 暗背景 → 亮前景


def Badge(props: dict) -> Element:
    """React Ink ``<Badge>`` 等价物：背景色块徽章控件。

    Props:
        label: 徽章文本。
        color: 背景色（颜色名/int；默认 ``"cyan"``）。
        fg: 前景色（颜色名/int；缺省按背景亮度自动对比）。
        bold: 是否加粗（默认 False）。
        padding: 左右内边距（默认 1）。
        style: 完整样式（与上述字段合并；style 优先）。

    Returns:
        TEXT 元素（``  label  `` 背景色块）。
    """
    label = str(props.get("label") or "")
    bg = _color(props.get("color"), 6)
    fg = _color(props.get("fg")) if props.get("fg") is not None else _badge_fg_for_bg(bg)
    try:
        padding = max(0, int(props.get("padding", 1)))
    except (TypeError, ValueError, OverflowError):
        padding = 1
    style = Style(fg=fg, bg=bg, bold=bool(props.get("bold", False)))
    base = props.get("style")
    if base is not None:
        # ★ P3（review）：合并方向修复——docstring「style 优先」即用户 style
        #   覆盖自动对比色。Style.merge 语义为「后者覆盖前者」：原
        #   ``base.merge(style)`` 让自动 fg 覆盖用户 style.fg（与文档矛盾）；
        #   改为 ``style.merge(base)``——用户 style 的 non-None 字段覆盖自动
        #   计算值（fg/bg/bold），未提供字段保留自动对比色。
        style = style.merge(base)
    text = " " * padding + label + " " * padding
    return h(TEXT, {"children": text, "style": style})


# ═══════════════════════════════════════════════════════════
# Divider — 分隔线
# ═══════════════════════════════════════════════════════════

_DIVIDER_DEFAULT_WIDTH = 40


def _truncate_to_width(text: str, max_w: int) -> str:
    """按显示宽度截断标题（不拆 CJK；超宽时末尾补省略号）。

    与 codeblock._truncate_to_width 同思路（保留 ``max_w-1`` 字符宽 +
    省略号 1 宽，返回宽度 <= max_w）；``max_w <= 0`` 返回空串。
    """
    if max_w <= 0:
        return ""
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


def Divider(props: dict) -> Element:
    """React Ink 风格分隔线控件（``ink-divider`` 对齐）。

    Props:
        title: 中间标题（None 时输出纯分隔线）。
        width: 总显示宽度（None 时自动：有标题 = 标题宽 + 4；无标题 = 40）。
        char: 分隔字符（默认 ``"─"``；支持宽字符按宽度换算）。
        color: 分隔线前景色（颜色名/int）。
        style: 分隔线完整样式（``color`` 覆盖 style.fg）。
        titleStyle: 标题样式（默认 None）。
        trailing: 右侧内容（可选——StyledRun 列表或 ink ``Line``；提供时
            输出「左侧填充 + 右侧内容」，行宽恒 = width；供 InputArea
            CPU/MEM、时间戳分隔线等右侧内容场景——与 ``_theme.sep_line``
            构建语义对齐，控件化表达）。

    Returns:
        无标题且无 trailing：TEXT 元素；有 trailing：Row 元素（左侧填充
        TEXT + 右侧内容 TEXT）；有标题：Row 元素（线+空格+标题+空格+线）。
    """
    title = props.get("title")
    title = None if title is None else str(title)
    char = str(props.get("char", "─")) or "─"
    width = props.get("width")
    if width is not None:
        try:
            width = max(1, int(width))
        except (TypeError, ValueError, OverflowError):
            width = None
    if width is None:
        width = wcswidth_simple(title) + 4 if title else _DIVIDER_DEFAULT_WIDTH
    hz_style = _resolve_style(props)
    title_style = props.get("titleStyle")

    # ★ trailing 右侧内容（方案B）：左侧填充 + 右侧内容（行宽恒 = width）。
    #   供 InputArea 分隔线（CPU/MEM、时间戳）等右侧内容场景——与
    #   ``_theme.sep_line`` 语义对齐（控件化表达，行宽不变量保持）。
    trailing = props.get("trailing")
    if trailing is not None and not title:
        trailing_runs = list(trailing.runs) if hasattr(trailing, "runs") else list(trailing)
        trailing_w = sum(getattr(r, "width", len(str(getattr(r, "text", "")))) for r in trailing_runs)
        if width > 0 and trailing_w > width:
            # 防御：右侧内容超宽时截断至 width（复用 _theme.sep_line 语义）
            from ..helpers import truncate_line
            if hasattr(trailing, "runs"):
                line = truncate_line(trailing, width)
                trailing_runs = list(line.runs)
            else:
                from src.tui.ink.output import Line as _L
                line = truncate_line(_L(list(trailing_runs)), width)
                trailing_runs = list(line.runs)
            trailing_w = sum(r.width for r in trailing_runs)
        fill = max(0, width - trailing_w)
        return h(Row, None, [
            h(TEXT, {"children": _repeat_to_width(char, fill), "style": hz_style}),
            h(TEXT, {"styled": list(trailing_runs)}),
        ])

    if not title:
        return h(TEXT, {"children": _repeat_to_width(char, width), "style": hz_style})

    title_w = wcswidth_simple(title)
    avail = width - title_w - 2  # 两侧各留 1 空格
    if avail <= 0:
        # ★ P3（review）：显式 width 极小且标题超宽时原样返回超宽 TEXT（超宽
        #   行破坏行宽不变量）——先截断标题至可用宽度（``width - 2`` = 标题
        #   模式中标题的最大剩余宽度），再按正常两侧线布局对齐到 width。
        title = _truncate_to_width(title, max(0, width - 2))
        title_w = wcswidth_simple(title)
        avail = width - title_w - 2
        if avail <= 0:
            # 截断后仍放不下（width <= 2）：仅标题（已截断到 width，不超宽）
            return h(TEXT, {"children": title, "style": title_style})
    left_w = avail // 2
    right_w = avail - left_w
    # ★ 阶段2（标准布局容器重构）：row BOX → Row（语义化门面，输出等价）。
    return h(Row, None, [
        h(TEXT, {"children": _repeat_to_width(char, left_w), "style": hz_style}),
        h(TEXT, {"children": " "}),
        h(TEXT, {"children": title, "style": title_style}),
        h(TEXT, {"children": " "}),
        h(TEXT, {"children": _repeat_to_width(char, right_w), "style": hz_style}),
    ])


__all__ = ["Badge", "Divider"]
