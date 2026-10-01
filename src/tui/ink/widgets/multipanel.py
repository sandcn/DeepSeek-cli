"""multipanel — 多面板分屏容器（React Ink 风格布局控件）。

一条 `MultiPanel` 把多个面板按水平（左右分栏）或垂直（上下分屏）方向排布，
每个面板带可选标题/边框，支持按比例分配尺寸与「活动面板」高亮 + Tab 切换。

用法::

    h(MultiPanel, {
        "direction": "horizontal",
        "panels": [
            {"title": "对话", "content": [...], "size": 2},
            {"title": "工具日志", "content": [...], "size": 1},
            {"title": "系统状态", "content": [...], "size": 1},
        ],
        "activeIndex": model.active_panel,
        "onActiveChange": set_active,
    })

依赖：element / hooks / core.style（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

from typing import Any

from ..element import BOX, TEXT, Element, h
from ..hooks import use_input, use_state
from src.tui.core.style import Style
from ._widget_common import _border_count, _call, _outer_height

__all__ = ["MultiPanel"]

#: 活动面板边框高亮色（256 色号：亮青）
_ACTIVE_BORDER = 45
#: 非活动面板边框色
_INACTIVE_BORDER = 240


def _normalize_content(content: Any) -> list:
    if content is None:
        return []
    if isinstance(content, (list, tuple)):
        return list(content)
    return [content]


def _pane(props: dict, active: bool, equal_size: bool = True) -> Element:
    title = props.get("title")
    title = None if title is None else str(title)
    border_color = props.get("borderColor")
    if border_color is None:
        border_color = _ACTIVE_BORDER if active else _INACTIVE_BORDER

    border_n = _border_count(props)
    pane: dict = {
        "border": border_n,
        "borderStyle": props.get("borderStyle", "single"),
        "borderColor": border_color,
        "flexDirection": "column",
        "flexShrink": 1,
    }
    size = props.get("size")
    if size is None:
        # equalSize=False：未指定 size 的面板按内容自适应（不参与等分）
        pane["flexGrow"] = 1 if equal_size else 0
    else:
        try:
            pane["flexGrow"] = max(0, int(size))
        except (TypeError, ValueError, OverflowError):
            pane["flexGrow"] = 1
    for dim in ("width", "minWidth", "minHeight"):
        if props.get(dim) is not None:
            pane[dim] = props[dim]
    # ★ P1（review 修复）：``height`` 语义 = 面板内容行数 → 换算 BOX 总高
    #   （+上下边框+内边距），避免内容越出边框/底边框被覆盖。
    if props.get("height") is not None:
        try:
            pane["height"] = _outer_height(pane, int(props["height"]))
        except (TypeError, ValueError, OverflowError):
            pass

    children: list = []
    if title:
        title_style = props.get("titleStyle")
        if title_style is None:
            title_style = Style(fg=_ACTIVE_BORDER if active else 242)
        children.append(h(TEXT, {"children": title, "style": title_style, "height": 1}))
    children.extend(_normalize_content(props.get("content")))
    return h(BOX, pane, *children)


def MultiPanel(props: dict) -> Element:
    """多面板分屏容器。

    Props:
        panels: 面板列表——每项 ``{"title"?, "content"?, "size"?, "border"?,
            "borderStyle"?, "borderColor"?, "titleStyle"?, "width"?, "height"?}``。
        direction: ``"horizontal"``（左右，默认）| ``"vertical"``（上下）。
        gap: 面板间距（默认 0）。
        activeIndex: 活动面板索引（受控；提供时非受控 state 不生效）。
        onActiveChange: 活动面板变化回调 ``(index) -> None``。
        focusable: 是否响应 Tab/Shift+Tab 切换活动面板（默认 True）。
        equalSize: 未指定 ``size`` 的面板是否等宽/等高（默认 True）。

    Returns:
        BOX 元素（``direction`` 方向的 flex 容器，内含分屏面板）。
    """
    raw = props.get("panels") or []
    if not isinstance(raw, (list, tuple)):
        raw = []
    panels = [p for p in raw if isinstance(p, dict)]
    n = len(panels)
    direction = props.get("direction", "horizontal")
    is_row = direction != "vertical"

    controlled = props.get("activeIndex") is not None
    state_index, set_state_index = use_state(0)
    try:
        active_index = int(props["activeIndex"]) if controlled else int(state_index)
    except (TypeError, ValueError, OverflowError):
        active_index = 0
    if n:
        active_index = max(0, min(n - 1, active_index))
    else:
        active_index = 0

    on_change = props.get("onActiveChange")
    focusable = props.get("focusable", True) is not False
    equal_size = props.get("equalSize", True) is not False

    def _set_active(idx: int) -> None:
        if n == 0:
            return
        idx = idx % n
        if not controlled:
            set_state_index(idx)
        _call(on_change, idx)

    def _handle(event) -> bool:
        if not focusable or n <= 1:
            return False
        kind = getattr(event, "kind", "")
        if kind == "tab":
            if getattr(event, "modifier", 0) == 2:
                _set_active(active_index - 1)
            else:
                _set_active(active_index + 1)
            return True
        if kind in ("arrow_left", "arrow_up"):
            _set_active(active_index - 1)
            return True
        if kind in ("arrow_right", "arrow_down"):
            _set_active(active_index + 1)
            return True
        return False

    use_input(_handle, {"isActive": bool(focusable)})

    gap = props.get("gap", 0)
    container = {
        "flexDirection": "row" if is_row else "column",
        "alignItems": "stretch",
        "gap": gap,
    }
    for key in ("width", "height", "border", "borderStyle", "borderColor"):
        if props.get(key) is not None:
            container[key] = props[key]

    children = [_pane(p, i == active_index, equal_size) for i, p in enumerate(panels)]
    return h(BOX, container, *children)
