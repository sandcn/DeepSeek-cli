"""MultiSelect — 多选列表控件（React Ink ink-multi-select 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——多选
列表独立成模块（公共辅助经 ``_interactive_common`` 共享）。

★ 全面控件化（2026-08-16 方案B）：控件扩展支持 TUI 弹窗界面（UserSelectPopup
多选）——新增 ``onCancel``（Esc 回调）、``onHighlight``（光标变化回调）、
``renderItem``（自定义行渲染）、``consumeAll``（弹窗模式消费所有按键）、
vim 风格 ``j/k/g/G`` 导航。未传新 props 时行为与旧版完全一致（零回归）。
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
from ._select_input import _is_vim_nav, _vim_navs_from_paste

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
        onCancel: ``(selected: list) -> None``——Esc 取消时回调（消费 Esc；
            None 时 Esc 放行）。
        onHighlight: ``(index) -> None``——光标下标变化时回调（导航后触发；
            None 时忽略）。
        renderItem: ``(item, index, isSelected, isChecked) -> Element``——
            自定义行渲染；None 时用默认 indicator+label。
        consumeAll: True（弹窗模式）——非导航/Enter/Esc 的按键也消费
            （阻断输入框）；Ctrl+C（``\\x03``）放行。

    行为：
      - up/down（及 vim j/k）移动光标；space（或空格 char）切换当前项选中；
      - g/G 跳首/末项；enter 触发 ``onSubmit(selected_values)`` 并消费事件；
      - escape 触发 ``onCancel(selected_values)``（onCancel 提供时）。

    Returns:
        BOX 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    onSubmit = props.get("onSubmit")
    on_cancel = props.get("onCancel")
    on_highlight = props.get("onHighlight")
    render_item = props.get("renderItem")
    focus = bool(props.get("focus", True))
    consume_all = bool(props.get("consumeAll", False))
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
    # ★ P3（review）：highlightStyle 改 ``is not None`` 判断——修复前 ``or``
    #   把显式空 Style()（falsy）当默认替换。
    highlight_style_prop = props.get("highlightStyle")
    highlight_style = highlight_style_prop if highlight_style_prop is not None else Style(fg=6)
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
    # ★ 滚动窗口 offset（2026-08-19，跟随光标滚动——与 SelectInput 同语义）：
    #   选项多于可见行数时 ↑/↓ 光标在窗口内逐行移动、越过窗口边界后窗口
    #   才滚动（能移动到未显示的行）。
    win_offset, set_win_offset = use_state(0)
    offset_ref = use_ref(0)
    offset_ref.current = win_offset

    def _scroll_follow(idx: int) -> None:
        """跟随光标滚动窗口（光标越过边界时推进 offset，保持光标可见）。"""
        new_off = _visible_window(idx, len(items), limit, offset_ref.current)[0]
        if new_off != offset_ref.current:
            offset_ref.current = new_off
            set_win_offset(new_off)

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
        if event.kind == "escape" and on_cancel is not None:
            # ★ P1（review）：onCancel 实参改为选中 values 列表——修复前传
            #   ``items[cur_cursor]``（当前光标项 dict），与 docstring 声明的
            #   ``(selected: list) -> None`` 契约不符（取消场景常需回填已勾
            #   选项）。与 onSubmit 的 ordered 收集逻辑一致（按 items 顺序、
            #   原始 value、_hashable 成员判断）。
            ordered = [
                item["value"] for item in items
                if _hashable(item["value"]) in cur_selected
            ]
            _call(on_cancel, ordered)
            return True
        navs: list = []
        if event.kind == "arrow_up":
            navs = ["up"]
        elif event.kind == "arrow_down":
            navs = ["down"]
        else:
            nav = _is_vim_nav(event)
            if nav is not None:
                navs = [nav]
            elif consume_all:
                # ★ 粘贴流逐字符导航（2026-08-19，与 SelectInput 同修复）：
                #   多字符 char 事件中的 j/k/g/G 逐个生效（渲染忙时导航键
                #   与 Enter 同批累积被 try_read_paste 判为粘贴——修复前
                #   整段被吞导航丢失）。仅 consumeAll（模态弹窗独占模式）
                #   启用，非弹窗消费方粘贴文本仍放行（零回归）。
                navs = _vim_navs_from_paste(event)
        moved = False
        new = cur_cursor
        for nav in navs:
            step = new
            if nav == "up":
                # ★ P3（review）：已在首项时按上键不移动——无效移动返回 False
                #   （不消费，放行父级；与 ListView/Menu 对齐）。
                if new > 0:
                    step = new - 1
            elif nav == "down":
                # ★ P3（review）：已在末项时按下键不移动——无效移动返回 False。
                if new < len(items) - 1:
                    step = new + 1
            elif nav == "first":
                if new != 0:
                    step = 0
            elif nav == "last":
                if new != len(items) - 1:
                    step = len(items) - 1
            if step != new:
                new = step
                moved = True
                # 逐字符逐键语义：每步同步 ref/state/onHighlight（与
                # SelectInput 同修复——对齐正常速度逐键分发行为）。
                cursor_ref.current = new
                set_cursor_idx(new)
                if on_highlight is not None:
                    _call(on_highlight, new)
        if moved:
            # ★ 跟随光标滚动（2026-08-19）：导航移动后窗口跟随（越过边界才
            #   滚动，光标行保持可见）。
            _scroll_follow(cursor_ref.current)
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
        if consume_all:
            # ★ 弹窗模式：其他按键一律消费（阻断输入框）；Ctrl+C 放行。
            if event.kind == "char" and event.char == "\x03":
                return False
            return True
        return False

    use_input(_handle, focus)

    # ★ P2（review）：渲染期钳制光标索引——items 收缩后到下一次按键前的帧内
    #   cursor_idx state 仍越界，钳制后光标行高亮不消失（与 SelectInput
    #   渲染期钳制一致；不改 state，事件期钳制会同步）。
    cursor_shown = _clamp_index(cursor_idx, len(items))
    # ★ 跟随光标滚动窗口（2026-08-19）：传当前 offset_ref（事件期推进后的
    #   即时值）——光标在窗口内窗口不动，越过边界才滚动。
    offset, count = _visible_window(cursor_shown, len(items), limit, offset_ref.current)
    rows = []
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_cursor = idx == cursor_shown
        is_checked = _hashable(item["value"]) in selected
        if render_item is not None:
            # ★ 自定义行渲染（方案B）：弹窗界面（UserSelectPopup 多选）经
            #   renderItem 完全自定义行元素（勾选/高亮由调用方表达）。异常
            #   降级默认行（防御）。
            try:
                child = render_item(item, idx, is_cursor, is_checked)
            except Exception:
                indicator = checked_prefix if is_checked else unchecked_prefix
                child = h(TEXT, {"children": indicator + item["label"], "style": highlight_style if is_cursor else None})
            if isinstance(child, Element):
                cp = dict(child.props)
                cp.setdefault("key", f"item-{idx}")
                rows.append(Element(child.type, cp, child.children))
            elif child is None:
                rows.append(h(TEXT, {"children": "", "key": f"item-{idx}"}))
            else:
                rows.append(h(TEXT, {"children": str(child), "key": f"item-{idx}"}))
            continue
        indicator = checked_prefix if is_checked else unchecked_prefix
        style = highlight_style if is_cursor else None
        rows.append(
            h(TEXT, {"children": indicator + item["label"], "style": style, "key": f"item-{idx}"})
        )
    # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
    return h(Column, None, rows)


__all__ = ["MultiSelect"]
