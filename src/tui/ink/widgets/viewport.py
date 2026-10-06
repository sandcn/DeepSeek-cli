"""viewport — 可滚动视口控件（React Ink 风格，独立可选层）。

React Ink 官方无「视口/滚动」组件（其为内容驱动流动模型）。本控件为框架
扩展：把一段内容行放进**固定高度窗口**，支持内容偏移（滚动）、滚动条与
键盘导航。作为独立容器使用，不影响现有「内容自然流入 scrollback」模型。

用法::

    h(Viewport, {"lines": ["a", "b", "c", ...], "height": 10})

键盘（``active=True`` 时经 ``use_input`` 消费）：↑↓ / j/k 单行、PgUp/PgDn
翻页、Home/End（g/G）首尾。滚动状态可受控（``offset`` prop + ``onOffsetChange``）
或非受控（内部 state）。

样式：
  - ``lineStyle`` / ``scrollbarStyle``：内容行 / 滚动条样式（``Style``）。
  - ``scrollbarChar``（默认 ``"█"``）/ ``trackChar``（默认 ``"│"``）。
"""

from __future__ import annotations

from typing import Any

from ..element import BOX, TEXT, Element, h
from ..output import StyledRun
from ..hooks import use_input, use_state
from src.tui.core.style import Style
from ..helpers import visual_width
from ._display_common import _truncate_to_width
from ._widget_common import _border_count, _call, _outer_height, use_wheel_scroll

__all__ = ["Viewport"]

_TRACK_CHAR = "│"
_THUMB_CHAR = "█"
#: 单个滚轮步进的滚动行数（终端滚轮一格 3 行，与多数分页器一致）。
_WHEEL_LINES = 3

#: Viewport 自有 props（不透传给 BOX）。
_VIEWPORT_ONLY_KEYS = frozenset({
    "lines", "height", "offset", "onOffsetChange", "active", "showScrollbar",
    "lineStyle", "scrollbarStyle", "scrollbarChar", "trackChar", "children",
})


def _clamp(value: int, low: int, high: int) -> int:
    if high < low:
        return low
    return max(low, min(high, value))


def _to_text(line: Any) -> str:
    """内容行归一化为纯文本（str/Line/其他 str() 化）。"""
    if isinstance(line, str):
        return line
    plain = getattr(line, "plain", None)
    if isinstance(plain, str):
        return plain
    return str(line)


def _pad_truncate(text: str, width: int) -> str:
    """按显示宽度截断（复用 ``_truncate_to_width``，CJK/ANSI 安全）+ 右侧补空格。"""
    if width <= 0:
        return ""
    truncated = _truncate_to_width(text, width, True)
    pad = width - visual_width(truncated)
    if pad > 0:
        return truncated + " " * pad
    return truncated


def Viewport(props: dict) -> Element:
    """固定高度可滚动视口（React Ink 风格组件）。

    Props:
        lines: 内容行（str 或 ``ink.Line``）。
        height: **可见内容行数**（默认 10；BOX 总高自动叠加上下边框/内边距）。
        width: 内容+滚动条总宽（int；缺省由容器约束，不补空格）。
        offset: 受控偏移（int；提供时非受控内部 state 不生效）。
        onOffsetChange: 偏移变化回调 ``(offset) -> None``。
        active: 是否响应键盘（默认 True）。
        showScrollbar: 是否显示滚动条（默认 True）。
        border/title/...: 透传 BOX 容器属性。
        lineStyle/scrollbarStyle: 内容/滚动条样式。

    Returns:
        BOX 元素（固定高度窗口 + 滚动条）。
    """
    raw_lines = props.get("lines") or []
    if not isinstance(raw_lines, (list, tuple)):
        raw_lines = [raw_lines]
    texts = [_to_text(x) for x in raw_lines]
    total = len(texts)

    try:
        height = max(1, int(props.get("height", 10)))
    except (TypeError, ValueError, OverflowError):
        height = 10
    max_offset = max(0, total - height)

    show_bar = props.get("showScrollbar", True) is not False
    border_n = _border_count(props, 0)
    width_prop = props.get("width")
    try:
        width = int(width_prop) if width_prop is not None else None
    except (TypeError, ValueError, OverflowError):
        width = None
    if width is not None and width <= 0:
        width = None

    controlled = "offset" in props and props.get("offset") is not None
    state_offset, set_state_offset = use_state(0)
    try:
        offset = int(props["offset"]) if controlled else int(state_offset)
    except (TypeError, ValueError, OverflowError):
        offset = 0
    offset = _clamp(offset, 0, max_offset)

    active = props.get("active", True) is not False
    on_offset_change = props.get("onOffsetChange")

    def _apply(new_offset: int) -> None:
        new_offset = _clamp(new_offset, 0, max_offset)
        if not controlled:
            set_state_offset(new_offset)
        _call(on_offset_change, new_offset)

    def _handle(event) -> bool:
        kind = getattr(event, "kind", "")
        if kind in ("arrow_up",):
            _apply(offset - 1)
            return True
        if kind in ("arrow_down",):
            _apply(offset + 1)
            return True
        if kind == "page_up":
            _apply(offset - height)
            return True
        if kind == "page_down":
            _apply(offset + height)
            return True
        if kind == "home":
            _apply(0)
            return True
        if kind == "end":
            _apply(max_offset)
            return True
        if kind == "char":
            ch = getattr(event, "char", "")
            if ch == "j":
                _apply(offset + 1)
                return True
            if ch == "k":
                _apply(offset - 1)
                return True
            if ch == "g":
                _apply(0)
                return True
            if ch == "G":
                _apply(max_offset)
                return True
        return False

    use_input(_handle, {"isActive": bool(active)})
    # ★ 鼠标滚轮（2026-10-07 鼠标支持）：滚轮上下各滚动 _WHEEL_LINES 行
    #   （视口控件是纯滚动语义——不改内容，只改偏移）。
    use_wheel_scroll(lambda delta: _apply(offset + delta * _WHEEL_LINES), bool(active))

    line_style = props.get("lineStyle")
    bar_style = props.get("scrollbarStyle") or Style(fg=240)
    thumb_char = str(props.get("scrollbarChar") or _THUMB_CHAR)
    track_char = str(props.get("trackChar") or _TRACK_CHAR)

    # 内容列预算：外宽 - 左右边框 - 滚动条列（修复前未扣除边框，内容行按
    # 外宽补空格后在边框内宽下折行，破坏「每行一内容行」的行数语义）。
    content_width = width
    if content_width is not None:
        content_width -= 2 * border_n
        if show_bar:
            content_width -= 1
        if content_width < 1:
            content_width = 1

    # 滚动条几何：thumb 位置/长度按内容比例（顶部对齐）。
    if show_bar and total > 0:
        thumb_len = max(1, int(round(height * height / total)))
        thumb_len = min(height, thumb_len)
        thumb_start = 0
        if max_offset > 0:
            thumb_start = int(round(offset * (height - thumb_len) / max_offset))
    else:
        thumb_len = 0
        thumb_start = 0

    children = []
    for i in range(height):
        idx = offset + i
        content = texts[idx] if 0 <= idx < total else ""
        if content_width is not None and content_width > 0:
            content = _pad_truncate(content, content_width)
        runs = [StyledRun(content, line_style)]
        if show_bar:
            if thumb_len and thumb_start <= i < thumb_start + thumb_len:
                runs.append(StyledRun(thumb_char, bar_style))
            else:
                runs.append(StyledRun(track_char, Style(fg=238)))
        children.append(h(TEXT, {"styled": runs}))

    box_props = {k: v for k, v in props.items() if k not in _VIEWPORT_ONLY_KEYS}
    box_props.setdefault("flexDirection", "column")
    if width is not None:
        box_props["width"] = width
    # ★ P1（review 修复）：``height`` 语义 = **可见内容行数**，须换算为 BOX
    #   总高（+上下边框+上下内边距）——修复前直接写 ``height`` 作为 BOX 总高：
    #   传 ``border`` 时内容行越出边框 / 底边框被正文覆盖。
    box_props["height"] = _outer_height(box_props, height, default_border=0)
    return h(BOX, box_props, *children)
