"""MultiSelect — 多选列表控件（React Ink ink-multi-select 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——多选
列表独立成模块（公共辅助经 ``_interactive_common`` 共享）。

★ 全面控件化（2026-08-16 方案B）：控件扩展支持 TUI 弹窗界面（UserSelectPopup
多选）——新增 ``onCancel``（Esc 回调）、``onHighlight``（光标变化回调）、
``renderItem``（自定义行渲染）、``consumeAll``（弹窗模式消费所有按键）、
vim 风格 ``j/k/g/G`` 导航。未传新 props 时行为与旧版完全一致（零回归）。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref, use_effect
from ..widgets.layout import Column
from ._interactive_common import (
    _call,
    _normalize_items,
    _visible_window,
    _clamp_index,
    _hashable,
)
from ._select_input import _is_vim_nav, _vim_navs_from_paste

_logger = logging.getLogger(__name__)

#: 选中/未选中指示符（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25cf "   # ●
_UNCHECKED = "\u25cb "  # ○


def MultiSelect(props: dict) -> Element:
    """React Ink ``<MultiSelect>`` 等价物：多选列表控件。

    Props:
        items: list[str] 或 list[{"label": str, "value": Any}]。
        onSubmit: ``(selected: list) -> None``——Enter 确认时回调（选中的
            values 列表，保持 items 顺序）。
        onSelect: ``(item) -> None``——Enter 时回调**当前光标项**
            （``{"label", "value"}``，与 SelectInput 对齐）；提供时 Enter
            走 onSelect、不再触发 onSubmit（UserSelectPopup 的「✎ 其他
            （自定义输入…）」伪选项需要拿到确定项判断进入输入模式）。
        onChange: ``(selected: list) -> None``——勾选集合变化时回调
            （空格切换后；ordered values，保持 items 顺序）。
        canToggle: ``(index, item) -> bool``——可选；返回 False 时该行
            不可勾选（空格被消费但不改变选中状态）。用于列表末尾的
            「自定义输入」伪选项行（不是可提交的勾选项）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始光标下标（默认 0）。
        index: 受控光标下标（默认 None 非受控）——外部权威源（如数字键
            快选切换勾选后写入的 us.selected）变化时渲染期同步光标。
        initialValues: 初始选中的 value 列表（默认 []）。
        values: 受控勾选集合（value 列表，默认 None 非受控）——外部权威源
            变化时渲染期同步内部勾选状态（数字键快选切换勾选后即时回显）。
        limit: 可见 item 数（超出滚动窗口；默认 None 全部显示）。
        onWindowChange: ``(offset, count, total) -> None``——可见窗口
            变化回调（渲染提交期触发；用于弹窗显示「上方/下方还有 N 项」）。
            默认 None 不回调（零回归）。
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
      - g/G 跳首/末项；consumeAll（模态弹窗）下 Home/End 首末、
        PgUp/PgDn 翻页；
      - enter 触发 ``onSelect(item)``（提供时）或
        ``onSubmit(selected_values)`` 并消费事件；
      - escape 触发 ``onCancel(selected_values)``（onCancel 提供时）。

    Returns:
        BOX 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    onSubmit = props.get("onSubmit")
    # ★ 2026-10-21（用户需求：user_select 支持自定义输入）：新增三个可选
    #   props——未传入时行为与旧版完全一致（零回归）。
    on_select = props.get("onSelect")
    on_change = props.get("onChange")
    can_toggle = props.get("canToggle")
    on_cancel = props.get("onCancel")
    on_highlight = props.get("onHighlight")
    render_item = props.get("renderItem")
    focus = bool(props.get("focus", True))
    consume_all = bool(props.get("consumeAll", False))
    on_window_change = props.get("onWindowChange")
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
    # ★ 2026-10-21（user_select 操作体验优化）：受控勾选集合 ``values``——
    #   外部权威源（如数字键快选切换勾选后写入 us.checked）变化时渲染期
    #   同步（ref 即时生效 + set_selected 排队收敛），控件勾选态即时回显。
    #   None（缺省）保持纯非受控模式（既有调用零回归）。
    controlled_values = props.get("values")
    controlled_set = None
    if controlled_values is not None:
        if (
            not hasattr(controlled_values, "__iter__")
            or isinstance(controlled_values, (str, bytes))
        ):
            controlled_values = []
        controlled_set = {_hashable(v) for v in controlled_values}
        if controlled_set != selected_ref.current:
            selected_ref.current = controlled_set
            set_selected(controlled_set)
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

    # ★ 2026-10-21（user_select 操作体验优化）：受控光标 ``index``——外部
    #   权威源（数字键快选切换勾选后写入 us.selected）变化时渲染期同步
    #   （ref 即时生效 + set_cursor_idx 排队收敛），光标高亮不再停在旧行。
    #   None（缺省）保持纯非受控模式（既有调用零回归）。
    controlled_index = props.get("index")
    ci = 0
    if controlled_index is not None:
        try:
            ci = int(controlled_index)
        except (TypeError, ValueError, OverflowError):
            ci = 0
        ci = _clamp_index(ci, len(items))
        if ci != cursor_ref.current:
            cursor_ref.current = ci
            set_cursor_idx(ci)
        _scroll_follow(ci)

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
        # ★ 2026-10-21（user_select 操作体验优化）：Home/End/PgUp/PgDn 导航
        #   ——仅 consumeAll（模态弹窗独占模式）启用，其他调用方（补全弹窗
        #   consumeAll=False）行为零变化。
        if consume_all and event.kind in ("home", "end", "page_up", "page_down"):
            if event.kind == "home":
                target = 0
            elif event.kind == "end":
                target = len(items) - 1
            else:
                step = limit if isinstance(limit, int) and limit > 0 else 5
                target = cur_cursor + (
                    -step if event.kind == "page_up" else step
                )
            target = _clamp_index(target, len(items))
            if target != cur_cursor:
                cursor_ref.current = target
                set_cursor_idx(target)
                if on_highlight is not None:
                    _call(on_highlight, target)
                _scroll_follow(target)
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
        if event.kind == "char" and event.char == " ":
            # ★ P3（review 2026-08-22）：删除 ``event.kind == "space"`` 死分支——
            #   InputParser 从不产生 kind=="space"（其他控件均删该分支）。
            # ★ 2026-10-21（用户需求）：canToggle 返回 False 的行不可勾选
            #   （「自定义输入」伪选项行）——空格被消费但不改变选中集合。
            if can_toggle is not None:
                try:
                    if not can_toggle(cur_cursor, items[cur_cursor]):
                        return True
                except Exception:
                    # 防御：自定义判定异常按可勾选处理（不因回调异常吞掉空格）。
                    _logger.debug("MultiSelect canToggle 异常（按可勾选处理）", exc_info=True)
            value = items[cur_cursor]["value"]
            new_selected = set(cur_selected)
            hval = _hashable(value)
            if hval in new_selected:
                new_selected.discard(hval)
            else:
                new_selected.add(hval)
            selected_ref.current = new_selected
            set_selected(new_selected)
            if on_change is not None:
                # ordered 与 onSubmit 同口径：按 items 原始顺序、原始 value。
                ordered = [
                    item["value"] for item in items
                    if _hashable(item["value"]) in new_selected
                ]
                _call(on_change, ordered)
            return True
        if event.kind == "enter":
            # ★ 2026-10-21（用户需求）：onSelect 提供时优先——弹窗需要拿到
            #   **当前光标项**判断是否为「自定义输入」伪选项；其余调用方
            #   （未传 onSelect）行为不变（onSubmit）。
            if on_select is not None:
                _call(on_select, items[cur_cursor])
                return True
            if onSubmit is None:
                # ★ P3（review 2026-08-22）：onSubmit 未注册时放行（Return False，
                #   与 _select_input 的 on_select None 放行对齐），勿阻断父级。
                return False
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
    # ★ 受控光标（2026-10-21）：本帧以外部权威下标为准（set_cursor_idx 收敛
    #   有一帧延迟）；非受控时沿用内部 state。
    cursor_shown = _clamp_index(
        ci if controlled_index is not None else cursor_idx, len(items)
    )
    # ★ 跟随光标滚动窗口（2026-08-19）：传当前 offset_ref（事件期推进后的
    #   即时值）——光标在窗口内窗口不动，越过边界才滚动。
    offset, count = _visible_window(cursor_shown, len(items), limit, offset_ref.current)
    # ★ 2026-10-21（user_select 滚动指示）：可见窗口变化回调（渲染提交期）。
    def _report_window() -> None:
        if on_window_change is not None:
            _call(on_window_change, offset, count, len(items))

    use_effect(_report_window, (offset, count, len(items)))
    rows = []
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_cursor = idx == cursor_shown
        # ★ 受控 values（2026-10-21）：本帧以外部权威集合为准（set_selected
        #   排队收敛有一帧延迟）；非受控时沿用内部 state。
        is_checked = _hashable(item["value"]) in (
            controlled_set if controlled_set is not None else selected
        )
        if render_item is not None:
            # ★ 自定义行渲染（方案B）：弹窗界面（UserSelectPopup 多选）经
            #   renderItem 完全自定义行元素（勾选/高亮由调用方表达）。异常
            #   降级默认行（防御）。
            try:
                child = render_item(item, idx, is_cursor, is_checked)
            except Exception:
                # ★ P2（review）：不静默降级——记录 warning（与 listview
                #   renderItem 降级日志口径一致），便于排查自定义渲染异常。
                _logger.warning(
                    "MultiSelect renderItem 异常（idx=%s），降级默认行", idx,
                    exc_info=True,
                )
                indicator = checked_prefix if is_checked else unchecked_prefix
                child = h(TEXT, {"children": indicator + item["label"], "style": highlight_style if is_cursor else None})
            if isinstance(child, Element):
                cp = dict(child.props)
                cp.setdefault("key", f"item-{idx}")
                rows.append(Element(child.type, cp, child.children))
            elif child is None:
                rows.append(h(TEXT, {"children": "", "key": f"item-{idx}"}))
            else:
                # ★ P3（review）：非 Element/None 返回值（list/dict 等）会渲染
                #   repr 文本（如 ``"[...]"``）——记 warning 便于排查（保持
                #   ``str()`` 渲染兼容既有调用方）。
                _logger.warning(
                    "MultiSelect renderItem 返回非 Element 类型: %r", type(child),
                )
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
