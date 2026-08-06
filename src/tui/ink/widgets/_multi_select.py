"""MultiSelect — 多选列表控件（React Ink ink-multi-select 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——多选
列表独立成模块（公共辅助经 ``_interactive_common`` 共享）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Column
from ._interactive_common import (
    _call,
    _normalize_items,
    _visible_window,
    _clamp_index,
    _hashable,
)

#: 选中/未选中指示符（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25cf "   # ●
_UNCHECKED = "\u25cb "  # ○


def MultiSelect(props: dict) -> Element:
    """React Ink ``<MultiSelect>`` 等价物：多选列表控件。

    Props:
        items: list[str] 或 list[{"label": str, "value": Any}]。
        onSubmit: ``(selected: list) -> None``——Enter 确认时回调（选中的
            values 列表，保持 items 顺序）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始光标下标（默认 0）。
        initialValues: 初始选中的 value 列表（默认 []）。
        limit: 可见 item 数（超出滚动窗口；默认 None 全部显示）。
        highlightStyle: 光标行样式（默认 ``Style(fg=6)`` cyan）。
        checkedPrefix/uncheckedPrefix: 选中/未选中指示符
            （默认 ``"● "`` / ``"○ "``）。

    行为：
      - up/down 移动光标；space（或空格 char）切换当前项选中；
      - enter 触发 ``onSubmit(selected_values)`` 并消费事件。

    Returns:
        BOX 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    onSubmit = props.get("onSubmit")
    focus = bool(props.get("focus", True))
    limit = props.get("limit")
    if limit is not None:
        try:
            limit = max(1, int(limit))
        except (TypeError, ValueError, OverflowError):
            limit = None
    initial_index = 0
    try:
        initial_index = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial_index = 0
    if items:
        initial_index = min(initial_index, len(items) - 1)
    highlight_style = props.get("highlightStyle") or Style(fg=6)
    checked_prefix = str(props.get("checkedPrefix", _CHECKED))
    unchecked_prefix = str(props.get("uncheckedPrefix", _UNCHECKED))

    cursor_idx, set_cursor_idx = use_state(initial_index)
    initial_values = props.get("initialValues", [])
    # ★ P3（review）：initialValues 非 list/tuple/set 时被丢弃——修复为
    #   hasattr __iter__ 守卫：生成器/range 等可迭代值同样接受（修复前仅
    #   三类内建容器，其余可迭代被静默置空）。
    # ★ 2026-08-06：守卫排除 str/bytes——str 是 Iterable 但逐字符拆分会
    #   产生意外选中集（与 _table/listview 的守卫写法对齐）。
    if not hasattr(initial_values, "__iter__") or isinstance(initial_values, (str, bytes)):
        initial_values = []
    # ★ E9（不可哈希 initialValues 兜底）：initialValues 含 dict/list 等不可
    #   哈希元素时 ``set(initial_values)`` 抛 TypeError（渲染期崩溃）——逐项
    #   经 ``_hashable`` 归一化为可哈希键（不可哈希值带前缀字符串键）。
    # ★ P2（review）：惰性初始化（callable initial）——集合推导仅首帧求值，
    #   修复前每帧重复计算（结果被丢弃，大 initialValues 时浪费）。
    selected, set_selected = use_state(
        lambda: {_hashable(v) for v in initial_values}
    )
    # ★ ref 镜像（同批连续按键修复）：handler 读 ref 而非闭包 state。
    cursor_ref = use_ref(cursor_idx)
    selected_ref = use_ref(selected)
    cursor_ref.current = cursor_idx
    selected_ref.current = selected

    def _handle(event) -> bool:
        if not focus or not items:
            return False
        # ★ E8（items 动态缩小越界防护）：光标索引钳制到合法范围——items
        #   缩小后 cursor_ref.current 可能越界，space 分支
        #   ``items[cur_cursor]["value"]`` 越界被 router 吞掉。钳制后同步
        #   state（ref 与 state 一致，光标行高亮不消失）。
        cur_cursor = _clamp_index(cursor_ref.current, len(items))
        if cur_cursor != cursor_ref.current:
            cursor_ref.current = cur_cursor
            set_cursor_idx(cur_cursor)
        cur_selected = selected_ref.current
        if event.kind == "arrow_up":
            # ★ P3（review）：已在首项时按上键不移动——无效移动返回 False
            #   （不消费，放行父级；与 ListView/Menu 对齐）。
            if cur_cursor <= 0:
                return False
            new = cur_cursor - 1
            cursor_ref.current = new
            set_cursor_idx(new)
            return True
        if event.kind == "arrow_down":
            # ★ P3（review）：已在末项时按下键不移动——无效移动返回 False。
            if cur_cursor >= len(items) - 1:
                return False
            new = cur_cursor + 1
            cursor_ref.current = new
            set_cursor_idx(new)
            return True
        if event.kind == "space" or (event.kind == "char" and event.char == " "):
            value = items[cur_cursor]["value"]
            new_selected = set(cur_selected)
            hval = _hashable(value)
            if hval in new_selected:
                new_selected.discard(hval)
            else:
                new_selected.add(hval)
            selected_ref.current = new_selected
            set_selected(new_selected)
            return True
        if event.kind == "enter":
            # ★ E9：onSubmit ordered 按 items **原始 value** 收集（不归一化）——
            #   不可哈希 value（dict/list）原样输出；集合成员判断经 _hashable。
            ordered = [
                item["value"] for item in items
                if _hashable(item["value"]) in selected_ref.current
            ]
            _call(onSubmit, ordered)
            return True
        return False

    use_input(_handle, focus)

    # ★ P2（review）：渲染期钳制光标索引——items 收缩后到下一次按键前的帧内
    #   cursor_idx state 仍越界，钳制后光标行高亮不消失（与 SelectInput
    #   渲染期钳制一致；不改 state，事件期钳制会同步）。
    cursor_shown = _clamp_index(cursor_idx, len(items))
    offset, count = _visible_window(cursor_shown, len(items), limit)
    rows = []
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_cursor = idx == cursor_shown
        is_checked = _hashable(item["value"]) in selected
        indicator = checked_prefix if is_checked else unchecked_prefix
        style = highlight_style if is_cursor else None
        rows.append(
            h(TEXT, {"children": indicator + item["label"], "style": style, "key": f"item-{idx}"})
        )
    # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
    return h(Column, None, rows)


__all__ = ["MultiSelect"]
