"""search_input — React Ink 风格搜索输入控件（SearchInput）。

文本输入 + 实时过滤 + 结果列表选择（ink-search-input 风格）：

    - 输入文本实时过滤 ``items``（不区分大小写子串匹配）；
    - up/down 在过滤结果间移动光标；enter 触发 ``onSelect(item, index)``；
    - escape 清空查询（回到空查询全量列表）；
    - ``limit`` 控制结果可见行数（超出滚动窗口）。

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

__all__ = ["SearchInput"]


#: 默认结果高亮样式（青色 fg=6）
_SEARCH_HIGHLIGHT = Style(fg=6)

#: 默认查询标签样式（亮青 fg=45）
_SEARCH_PROMPT = Style(fg=45, bold=True)

#: 默认无结果提示样式（dim 灰 244）
_SEARCH_EMPTY = Style(fg=244)


def _normalize_items(items) -> list[dict]:
    """规范化 items 为 ``{"label": str, "value": Any, "searchText": str}``。"""
    if items is None:
        return []
    if not hasattr(items, "__iter__"):
        return []
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            label = str(item.get("label", item.get("value", "")))
            value = item.get("value", label)
        else:
            label = str(item)
            value = item
        search_text = str(item.get("searchText", label)) if isinstance(item, dict) else label
        out.append({"label": label, "value": value, "searchText": search_text})
    return out


def _filter_items(items: list[dict], query: str) -> list[dict]:
    """按查询过滤（不区分大小写子串匹配；空查询返回全量）。"""
    q = query.lower()
    if not q:
        return items
    return [it for it in items if q in it["searchText"].lower()]





def SearchInput(props: dict) -> Element:
    """React Ink ``<SearchInput>`` 等价物：搜索输入 + 过滤结果列表。

    Props:
        items: 候选列表（str 或 dict：label/value/searchText）。
        onSelect: ``(item, index) -> None``——Enter 确认（过滤后索引）。
        onQueryChange: ``(query: str) -> None``——查询变化回调。
        focus: 是否参与输入路由（默认 True）。
        placeholder: 空查询占位文本（默认 "Search..."）。
        limit: 结果可见行数（超出滚动窗口；默认 None 全部显示）。
        highlightStyle: 光标行样式（默认 ``Style(fg=6)`` cyan）。
        initialIndex: 初始光标下标（默认 0）。
        prefix: 查询行前缀（默认 ``"❯ "``）。

    行为：
      - 可打印字符追加到查询（实时过滤）；backspace 删除；escape 清空；
      - up/down 移动光标（过滤结果间）；enter 触发 ``onSelect``。

    Returns:
        Column 元素（查询行 + 过滤结果列表）。
    """
    all_items = _normalize_items(props.get("items", []))
    on_select = props.get("onSelect")
    on_query_change = props.get("onQueryChange")
    focus = bool(props.get("focus", True))
    placeholder = str(props.get("placeholder", "Search..."))
    limit = props.get("limit")
    if limit is not None:
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError, OverflowError):
            limit = None
    highlight_style = props.get("highlightStyle") or _SEARCH_HIGHLIGHT
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial_index = 0
    prefix = str(props.get("prefix", "❯ "))

    query, set_query = use_state("")
    cursor, set_cursor = use_state(initial_index)
    # ref 镜像（同批连续按键）：handler 读 ref
    query_ref = use_ref(query)
    cursor_ref = use_ref(cursor)
    query_ref.current = query
    cursor_ref.current = cursor

    def _handle(event) -> bool:
        if not focus:
            return False
        cur_q = query_ref.current
        cur_cursor = cursor_ref.current
        filtered = _filter_items(all_items, cur_q)
        if event.kind == "char":
            ch = event.char
            if not ch:
                return False
            if "\n" in ch or "\r" in ch:
                return False
            new_q = cur_q + ch
            query_ref.current = new_q
            cursor_ref.current = 0
            set_query(new_q)
            set_cursor(0)
            if on_query_change is not None:
                try:
                    on_query_change(new_q)
                except Exception:
                    pass
            return True
        if event.kind == "backspace":
            if cur_q:
                new_q = cur_q[:-1]
                query_ref.current = new_q
                cursor_ref.current = 0
                set_query(new_q)
                set_cursor(0)
                if on_query_change is not None:
                    try:
                        on_query_change(new_q)
                    except Exception:
                        pass
            return True
        if event.kind == "escape":
            if cur_q:
                query_ref.current = ""
                cursor_ref.current = 0
                set_query("")
                set_cursor(0)
                if on_query_change is not None:
                    try:
                        on_query_change("")
                    except Exception:
                        pass
            return True
        if event.kind == "arrow_up":
            if filtered and cur_cursor > 0:
                cursor_ref.current = cur_cursor - 1
                set_cursor(cursor_ref.current)
            return True
        if event.kind == "arrow_down":
            if filtered and cur_cursor < len(filtered) - 1:
                cursor_ref.current = cur_cursor + 1
                set_cursor(cursor_ref.current)
            return True
        if event.kind == "enter":
            if filtered:
                idx = _clamp_index(cur_cursor, len(filtered))
                item = filtered[idx]
                if on_select is not None:
                    try:
                        on_select(item, idx)
                    except Exception:
                        pass
            return True
        return False

    use_input(_handle, focus)

    filtered = _filter_items(all_items, query)
    cursor_shown = _clamp_index(cursor, len(filtered))
    # 可见窗口（limit 滚动）
    if limit is not None and len(filtered) > limit:
        offset = max(0, min(cursor_shown, len(filtered) - limit))
        shown = filtered[offset:offset + limit]
        shown_cursor = cursor_shown - offset
    else:
        shown = filtered
        shown_cursor = cursor_shown

    children: list = []
    # 查询行（前缀 + 当前查询/占位符）
    query_text = query if query else placeholder
    children.append(h(TEXT, {
        "children": prefix + query_text,
        "style": _SEARCH_PROMPT if query else _SEARCH_EMPTY,
        "height": 1,
    }))
    if not filtered:
        if query:
            children.append(h(TEXT, {
                "children": f"  无匹配结果: {query}",
                "style": _SEARCH_EMPTY,
                "height": 1,
            }))
    else:
        for i, item in enumerate(shown):
            is_sel = i == shown_cursor
            style = highlight_style if is_sel else None
            children.append(h(TEXT, {
                "children": ("  " if not is_sel else "❯ ") + item["label"],
                "style": style,
                "height": 1,
                "key": f"sr-{i}",
            }))
    # ★ 标准布局：Column 纵向堆叠查询行 + 结果
    return h(Column, None, children)


__all__ = ["SearchInput"]
