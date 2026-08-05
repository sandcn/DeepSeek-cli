"""listview — React Ink 风格虚拟滚动列表（ListView）。

大列表渲染优化：只渲染可见窗口内的项（``height`` 视口 + 滚动 offset）——
渲染行数 = O(视口)，与 items 总数无关。复用 Column 布局 + 显式高度裁剪
（超出视口的项不创建 Element，避免大列表每帧全量布局）。

功能：
  - up/down 移动光标（自动滚出视口：光标越过视口边界时滚动 offset）；
  - home/end 跳到首/末项；enter 触发 ``onSelect(item, index)``；
  - ``initialIndex`` 初始光标；items 变化时光标/offset 钳制。

依赖约束：仅依赖 element / output / core.style / hooks / widgets.layout
（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Column
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_clamp_index 原本地定义——收敛
#   至 _widget_common 单一真源。
from ._widget_common import _clamp_index

__all__ = ["ListView"]


def ListView(props: dict) -> Element:
    """React Ink 风格虚拟滚动列表控件（``ink-listview`` 对齐）。

    Props:
        items: 列表项（任意类型；经 ``renderItem`` 渲染）。
        height: 可见行数（视口高度，默认 10）。
        renderItem: ``(item, index) -> Element``——项渲染函数
            （缺省 ``lambda item, i: h(TEXT, {"children": str(item), "height": 1})``）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始光标下标（默认 0）。
        onSelect: ``(item, index) -> None``——Enter 选择回调。
        highlightStyle: 光标行样式（默认 ``Style(fg=6)`` cyan）。

    行为（与常见 React 列表选择控件对齐）：
      - arrow_up/arrow_down 移动光标（光标越过视口边界时自动滚动 offset）；
      - home/end 跳到首/末项（自动滚动到目标可见）；
      - enter 触发 ``onSelect(items[cursor], cursor)``。

    Returns:
        Column 元素（高度 = ``height`` 视口，仅渲染可见窗口内的项）。
    """
    raw_items = props.get("items")
    # ★ 健壮性（渲染错误防御）：items 不可迭代（None/标量/对象）时回退空列表
    #   ——修复前 ``list(props.get("items", []) or [])`` 对不可迭代的 items
    #   （如 float/bool）抛 TypeError，ListView 渲染崩溃。
    if raw_items is None:
        items = []
    elif hasattr(raw_items, "__iter__") and not isinstance(raw_items, (str, bytes)):
        items = list(raw_items)
    else:
        items = []
    try:
        viewport_h = max(1, int(props.get("height", 10)))
    except (TypeError, ValueError, OverflowError):
        viewport_h = 10
    render_item = props.get("renderItem")
    if render_item is None:
        render_item = lambda item, i: h(TEXT, {"children": str(item), "height": 1})
    on_select = props.get("onSelect")
    focus = bool(props.get("focus", True))
    highlight_style = props.get("highlightStyle") or Style(fg=6)
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial_index = 0
    total = len(items)

    cursor, set_cursor = use_state(_clamp_index(initial_index, total))
    offset, set_offset = use_state(0)
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 state。
    cursor_ref = use_ref(cursor)
    offset_ref = use_ref(offset)
    cursor_ref.current = cursor
    offset_ref.current = offset

    def _handle(event) -> bool:
        if not focus or total == 0:
            return False
        cur = _clamp_index(cursor_ref.current, total)
        cur_offset = offset_ref.current
        changed = False
        if event.kind == "arrow_up":
            cur = _clamp_index(cur - 1, total)
            changed = True
        elif event.kind == "arrow_down":
            cur = _clamp_index(cur + 1, total)
            changed = True
        elif event.kind == "home":
            cur = 0
            changed = True
        elif event.kind == "end":
            cur = total - 1
            changed = True
        elif event.kind == "enter":
            if on_select is not None:
                try:
                    on_select(items[cur], cur)
                except Exception:
                    pass
            return True
        if not changed:
            return False
        # 光标移出视口 → 滚动 offset（保持光标可见）
        if cur < cur_offset:
            cur_offset = cur
        elif cur >= cur_offset + viewport_h:
            cur_offset = cur - viewport_h + 1
        cursor_ref.current = cur
        offset_ref.current = cur_offset
        set_cursor(cur)
        set_offset(cur_offset)
        return True

    use_input(_handle, focus)

    # 渲染期钳制（items 收缩后光标/offset 越界防护）
    cursor_shown = _clamp_index(cursor, total)
    max_offset = max(0, total - viewport_h)
    offset_shown = min(offset, max_offset) if total > viewport_h else 0
    if cursor_shown < offset_shown:
        offset_shown = cursor_shown
    if cursor_shown >= offset_shown + viewport_h and total > viewport_h:
        offset_shown = max(0, cursor_shown - viewport_h + 1)
    rows: list = []
    for i in range(offset_shown, min(total, offset_shown + viewport_h)):
        item = items[i]
        is_sel = i == cursor_shown
        child = render_item(item, i)
        if isinstance(child, Element):
            cp = dict(child.props)
            if is_sel and "style" not in cp and "styled" not in cp:
                cp["style"] = highlight_style
            cp.setdefault("key", f"lv-{i}")
            rows.append(Element(child.type, cp, child.children))
        else:
            rows.append(h(TEXT, {
                "children": str(child),
                "style": highlight_style if is_sel else None,
                "key": f"lv-{i}", "height": 1,
            }))
    # ★ 标准布局：Column 显式 height 视口裁剪（超出部分不渲染——虚拟化）
    return h(Column, {"height": viewport_h, "width": props.get("width")}, rows)


__all__ = ["ListView"]
