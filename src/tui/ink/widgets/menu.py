"""menu — React Ink 风格垂直菜单控件（Menu）。

垂直选项列表 + 键盘导航（up/down + enter 选择），支持快捷键标签（右侧
对齐）、禁用项（不可选但可见）、分组标题（``type="header"``——不可聚焦）。

节点形态：:

    items = [
        {"label": "文件", "type": "header"},          # 分组标题（不可选）
        {"label": "新建", "shortcut": "N", "onSelect": ...},
        {"label": "打开", "shortcut": "O", "disabled": True},
        {"label": "退出", "shortcut": "Q"},
    ]

- 光标仅在可选项（非 header、非 disabled）间移动；
- 快捷键标签右侧对齐（``align="right"``——行内两端对齐）；
- enter 触发 ``onSelect(item, index)``（若 item 自带 ``onSelect`` 优先调用）。

依赖约束：仅依赖 element / output / core.style / _screen / hooks /
widgets.layout（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Row, Column

_logger = logging.getLogger(__name__)
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_clamp_index 原本地定义（与
#   _interactive_common/tabs/search_input/tree/listview 逐字重复）——收敛至
#   _widget_common 单一真源。
from ._widget_common import _clamp_index

__all__ = ["Menu"]


#: 默认光标行样式（青色 fg=6）
_MENU_HIGHLIGHT = Style(fg=6, bold=True)

#: 默认快捷键标签样式（dim 灰 244）
_MENU_SHORTCUT = Style(fg=244)

#: 默认禁用项样式（dim 灰 238）
_MENU_DISABLED = Style(fg=238)

#: 默认分组标题样式（青 45 + bold）
_MENU_HEADER = Style(fg=45, bold=True)





def _is_selectable(item: dict) -> bool:
    """菜单项是否可聚焦/选择（非分组标题、非禁用）。"""
    if item.get("type") == "header":
        return False
    if item.get("disabled"):
        return False
    return True


def _next_selectable(items: list, cur: int, delta: int) -> int:
    """从 cur 出发沿方向找下一个可选项（循环；无则返回 cur）。"""
    n = len(items)
    if n == 0:
        return cur
    for _ in range(n):
        cur = (cur + delta) % n
        if _is_selectable(items[cur]):
            return cur
    return cur


def Menu(props: dict) -> Element:
    """React Ink 风格垂直菜单控件。

    Props:
        items: 菜单项列表（str 简写或 dict：label/type/shortcut/disabled/
            onSelect/onHighlight）。
        onSelect: ``(item, index) -> None``——Enter 选择回调（item 自带
            ``onSelect`` 时优先调用，``(item, index)``）。
        onHighlight: ``(item, index) -> None``——光标移动回调。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始光标下标（默认 0；自动跳到最近可选项）。
        highlightStyle: 光标行样式（默认 ``Style(fg=6, bold=True)`` cyan bold）。
        shortcutStyle: 快捷键标签样式（默认 ``Style(fg=244)``）。
        disabledStyle: 禁用项样式（默认 ``Style(fg=238)``）。
        headerStyle: 分组标题样式（默认 ``Style(fg=45, bold=True)``）。
        shortcutAlign: 快捷键对齐（"right" 行内两端对齐 / "left" 紧跟标签，
            默认 "right"）。
        minShortcutGap: 快捷键与标签最小间距（默认 2）。

    Returns:
        Column 元素（纵向堆叠的菜单行）。
    """
    raw_items = props.get("items", [])
    # 规范化 items（str → dict；不可迭代兜底空列表）
    items: list[dict] = []
    if hasattr(raw_items, "__iter__") and not isinstance(raw_items, (str, bytes)):
        for it in raw_items:
            if isinstance(it, dict):
                items.append(dict(it))
            else:
                items.append({"label": str(it)})
    on_select = props.get("onSelect")
    on_highlight = props.get("onHighlight")
    focus = bool(props.get("focus", True))
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial_index = 0
    if items:
        initial_index = _clamp_index(initial_index, len(items))
        if not _is_selectable(items[initial_index]):
            initial_index = _next_selectable(items, initial_index, 1)
    highlight_style = props.get("highlightStyle") or _MENU_HIGHLIGHT
    shortcut_style = props.get("shortcutStyle") or _MENU_SHORTCUT
    disabled_style = props.get("disabledStyle") or _MENU_DISABLED
    header_style = props.get("headerStyle") or _MENU_HEADER
    shortcut_align = props.get("shortcutAlign", "right")
    try:
        min_gap = max(0, int(props.get("minShortcutGap", 2)))
    except (TypeError, ValueError, OverflowError):
        min_gap = 2

    cursor, set_cursor = use_state(initial_index)
    # ref 镜像（同批连续按键）：handler 读 ref
    cursor_ref = use_ref(cursor)
    cursor_ref.current = cursor

    def _handle(event) -> bool:
        if not focus or not items:
            return False
        cur = _clamp_index(cursor_ref.current, len(items))
        # ★ P3（review）：items 收缩后 state 未同步——钳制结果与 ref 不一致时
        #   同步（修复前仅不可选时跳转同步，越界钳制到可选值不 set_cursor）。
        if cur != cursor_ref.current:
            cursor_ref.current = cur
            set_cursor(cur)
        if not _is_selectable(items[cur]):
            cur = _next_selectable(items, cur, 1)
            cursor_ref.current = cur
            set_cursor(cur)
        if event.kind == "arrow_up":
            new = _next_selectable(items, cur, -1)
            if new != cur:
                cursor_ref.current = new
                set_cursor(new)
                if on_highlight is not None:
                    try:
                        on_highlight(items[new], new)
                    except Exception:
                        _logger.debug("Menu onHighlight 回调异常", exc_info=True)
            return True
        if event.kind == "arrow_down":
            new = _next_selectable(items, cur, 1)
            if new != cur:
                cursor_ref.current = new
                set_cursor(new)
                if on_highlight is not None:
                    try:
                        on_highlight(items[new], new)
                    except Exception:
                        _logger.debug("Menu onHighlight 回调异常", exc_info=True)
            return True
        if event.kind in ("home", "end"):
            new = _next_selectable(items, 0, 1) if event.kind == "home" else _next_selectable(items, len(items) - 1, -1)
            cursor_ref.current = new
            set_cursor(new)
            if on_highlight is not None:
                try:
                    on_highlight(items[new], new)
                except Exception:
                    _logger.debug("Menu onHighlight 回调异常", exc_info=True)
            return True
        if event.kind == "enter":
            item = items[cur]
            item_on_select = item.get("onSelect")
            if item_on_select is not None:
                try:
                    item_on_select(item, cur)
                except Exception:
                    _logger.debug("Menu item onSelect 回调异常", exc_info=True)
            elif on_select is not None:
                try:
                    on_select(item, cur)
                except Exception:
                    _logger.debug("Menu onSelect 回调异常", exc_info=True)
            return True
        return False

    use_input(_handle, focus)

    # ★ 健壮性（渲染错误防御）：items 为空时渲染空 TEXT（h=0 不占行）——
    #   修复前渲染期钳制 ``_clamp_index(cursor, 0)`` 返回 0 后
    #   ``items[cursor_shown]`` 越界抛 IndexError，Menu 空 items 渲染崩溃。
    if not items:
        return h(TEXT, {"children": ""})
    # 渲染期钳制光标到可选项
    cursor_shown = _clamp_index(cursor, len(items))
    if not _is_selectable(items[cursor_shown]):
        cursor_shown = _next_selectable(items, cursor_shown, 1)
    # 计算标签最大宽（快捷键右对齐定位）
    max_label_w = 0
    for it in items:
        if it.get("type") != "header":
            w = wcswidth_simple(str(it.get("label", "")))
            if w > max_label_w:
                max_label_w = w
    rows: list = []
    for i, item in enumerate(items):
        label = str(item.get("label", ""))
        # 强制单行（label 可能含换行——归一化防行级 diff 宽度不变量破坏）
        if "\n" in label:
            label = label.replace("\n", " ")
        is_header = item.get("type") == "header"
        is_disabled = bool(item.get("disabled"))
        # ★ P3（review）：is_sel 增加 not is_disabled——禁用项即使光标钳制在其
        #   行也不高亮（修复前 disabled 项被高亮样式覆盖 disabledStyle）。
        is_sel = i == cursor_shown and not is_header and not is_disabled
        shortcut = str(item.get("shortcut", "")) if item.get("shortcut") else ""
        if is_header:
            rows.append(h(TEXT, {
                "children": label, "style": header_style, "height": 1,
                "key": f"menu-{i}",
            }))
            continue
        style = None
        if is_sel:
            style = highlight_style
        elif is_disabled:
            style = disabled_style
        if shortcut and shortcut_align == "right":
            # 行内两端对齐：标签 + 间距 + 快捷键（右侧对齐）
            pad = max(min_gap, max_label_w - wcswidth_simple(label) + min_gap)
            rows.append(h(Row, {"height": 1}, [
                h(TEXT, {"children": label, "style": style, "height": 1}),
                h(TEXT, {"children": " " * pad, "height": 1}),
                h(TEXT, {"children": shortcut, "style": shortcut_style, "height": 1}),
            ]))
        elif shortcut:
            rows.append(h(TEXT, {
                "children": f"{label}  {shortcut}", "style": style, "height": 1,
                "key": f"menu-{i}",
            }))
        else:
            rows.append(h(TEXT, {
                "children": label, "style": style, "height": 1,
                "key": f"menu-{i}",
            }))
    # ★ 标准布局：Column 纵向堆叠菜单行
    return h(Column, None, rows)


__all__ = ["Menu"]
