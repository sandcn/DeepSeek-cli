"""breadcrumbs — React Ink 风格面包屑导航展示控件（Breadcrumbs）。

层级路径展示（``Home / Docs / Guide``），支持自定义分隔符与样式：

    items = [{"label": "Home", "active": False}, {"label": "Docs"}, "Guide"]

- ``active`` 项高亮（默认 fg=45 bold）；非活跃项默认 fg=244；
- ``separator`` 自定义分隔符（默认 ``" / "``），支持 ``separatorStyle``；
- ``maxItems`` 折叠（超出显示 ``…`` 后省略中间项，保留首尾）。

依赖约束：仅依赖 element / output / core.style / hooks / widgets.layout
（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..widgets.layout import Row

__all__ = ["Breadcrumbs"]


#: 默认分隔符样式（dim 灰 240）
_BREADCRUMB_SEP = Style(fg=240)

#: 默认活跃项样式（青 fg=45 + bold）
_BREADCRUMB_ACTIVE = Style(fg=45, bold=True)

#: 默认非活跃项样式（灰 252）
_BREADCRUMB_ITEM = Style(fg=252)


def _normalize_items(items) -> list[dict]:
    """规范化 items 为 ``{"label": str, "active": bool}``。"""
    if items is None:
        return []
    if not hasattr(items, "__iter__"):
        return []
    # ★ P2（review 2026-08-22）：str/bytes 是 Iterable（逐字符）——作为 items
    #   被逐字符拆成面包屑项（意外语义）；与 _interactive_common 守卫一致。
    if isinstance(items, (str, bytes)):
        return []
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            label = str(it.get("label", ""))
            active = bool(it.get("active", False))
        else:
            label = str(it)
            active = False
        # 强制单行（label 可能含换行——归一化防行级 diff 宽度不变量破坏）
        if "\n" in label:
            label = label.replace("\n", " ")
        out.append({"label": label, "active": active})
    return out


def _apply_max_items(items: list[dict], max_items: int) -> list[dict]:
    """maxItems 折叠：超出时保留首尾 + 中间 ``…``（占位项）。"""
    if max_items <= 0 or len(items) <= max_items:
        return items
    # 至少保留首 + 尾（max_items>=2 时）
    if max_items == 1:
        return [items[-1]]
    # ★ P2（review）：off-by-one 修复——head_n 由 max_items-1 改为 max_items-2，
    #   总项数 = head_n + 省略号 + 尾 = max_items（修复前 max_items+1，折叠后
    #   仍超 maxItems）。
    head_n = max_items - 2  # 首部保留数（尾部保留 1 + 省略号 1）
    out = items[:head_n] + [{"label": "…", "active": False, "_ellipsis": True}]
    out.append(items[-1])
    return out


def Breadcrumbs(props: dict) -> Element:
    """React Ink 风格面包屑展示控件。

    Props:
        items: 面包屑项（str 或 dict：label/active）。
        separator: 分隔符（默认 ``" / "``）。
        separatorStyle: 分隔符样式（默认 ``Style(fg=240)``）。
        activeStyle: 活跃项样式（默认 ``Style(fg=45, bold=True)``）。
        itemStyle: 非活跃项样式（默认 ``Style(fg=252)``）。
        maxItems: 最大项数（超出折叠；默认 None 不折叠）。
        height: 行高（默认 1）。

    Returns:
        Row 元素（横向排列的项 + 分隔符）。
    """
    items = _normalize_items(props.get("items", []))
    separator = str(props.get("separator", " / "))
    # ★ P3（review）：样式 prop 改 ``is not None`` 判断——修复前 ``or`` 把
    #   显式空 Style()（falsy）当默认替换。
    separator_style_prop = props.get("separatorStyle")
    separator_style = separator_style_prop if separator_style_prop is not None else _BREADCRUMB_SEP
    active_style_prop = props.get("activeStyle")
    active_style = active_style_prop if active_style_prop is not None else _BREADCRUMB_ACTIVE
    item_style_prop = props.get("itemStyle")
    item_style = item_style_prop if item_style_prop is not None else _BREADCRUMB_ITEM
    try:
        max_items = int(props.get("maxItems", 0))
    except (TypeError, ValueError, OverflowError):
        max_items = 0
    try:
        height = max(0, int(props.get("height", 1)))
    except (TypeError, ValueError, OverflowError):
        height = 1

    shown = _apply_max_items(items, max_items)
    children: list = []
    for i, item in enumerate(shown):
        style = active_style if item["active"] else item_style
        children.append(h(TEXT, {
            "children": item["label"], "style": style, "height": height,
            "key": f"bc-{i}",
        }))
        if i < len(shown) - 1:
            children.append(h(TEXT, {
                "children": separator, "style": separator_style, "height": height,
            }))
    # ★ 标准布局：Row 横向排列
    return h(Row, {"height": height}, children)


__all__ = ["Breadcrumbs"]
