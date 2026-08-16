"""SelectInput — 单选列表控件（React Ink ink-select-input 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/interactive.py`` 拆分——单选
列表独立成模块（公共辅助经 ``_interactive_common`` 共享）。

★ 全面控件化（2026-08-16 方案B）：控件扩展支持 TUI 弹窗界面（UserSelectPopup
单选 / CompletionPopup）——新增 ``onCancel``（Esc 回调）、``onHighlight``
（选中变化回调）、``renderItem``（自定义行渲染）、``consumeAll``（弹窗模式
消费所有按键）、vim 风格 ``j/k/g/G`` 导航（与 arrow 等价）。未传新 props
时行为与旧版完全一致（零回归）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Column
from ._interactive_common import (
    _call,
    _normalize_items,
    _visible_window,
    _clamp_index,
)


def _is_vim_nav(event) -> str | None:
    """vim 风格导航判定：返回 "down"/"up"/"first"/"last" 或 None。

    j/J 下、k/K 上、g 首、G 末（大小写等效——UserSelectPopup 既有语义）。
    非导航字符返回 None。
    """
    if event.kind == "char" and event.char in ("j", "J"):
        return "down"
    if event.kind == "char" and event.char in ("k", "K"):
        return "up"
    if event.kind == "char" and event.char in ("g", "G"):
        return "first" if event.char == "g" else "last"
    return None


def SelectInput(props: dict) -> Element:
    """React Ink ``<SelectInput>`` 等价物：单选列表选择控件。

    Props:
        items: list[str] 或 list[{"label": str, "value": Any}]。
        onSelect: ``(item) -> None``——Enter 确认时回调（item 为
            ``{"label", "value"}`` dict）。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始选中下标（默认 0）。
        limit: 可见 item 数（超出滚动窗口；默认 None 全部显示）。
        highlightStyle: 选中行样式（默认 ``Style(fg=6)`` cyan）。
        prefix: 选中行前缀（默认 ``"> "``）；未选中行用同宽空格对齐。
        onCancel: ``(item) -> None``——Esc 取消时回调（消费 Esc；None 时
            Esc 放行）。弹窗界面（UserSelectPopup）经此实现取消协议。
        onHighlight: ``(index) -> None``——选中下标变化时回调（导航
            ↑↓/j/k/g/G 后触发；None 时忽略）。
        renderItem: ``(item, index, isSelected) -> Element``——自定义行渲染
            （返回元素直接作为列表行；None 时用默认 prefix+label）。
        consumeAll: True（弹窗模式）——非导航/Enter/Esc 的按键也消费
            （返回 True 不放行），防止字符落入输入框；Ctrl+C（``\\x03``）
            仍放行（可中断工具执行）。

    Returns:
        BOX 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    on_select = props.get("onSelect")
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
    # ★ P3（review）：prefix=None 时回退默认 "> "——修复前 ``str(None)`` 渲染
    #   出字面 "None"。
    prefix = props.get("prefix", "> ")
    prefix = str(prefix) if prefix is not None else "> "

    selected, set_selected = use_state(initial_index)
    # ★ ref 镜像（同批连续按键修复）：handler 闭包捕获渲染期 state——同一渲染
    #   批次内多个事件（如按住 ↑/↓）之间无重渲染，闭包 state 陈旧。用 ref
    #   保存最新值（渲染期同步 + 事件期即时更新），handler 一律读 ref。
    selected_ref = use_ref(selected)
    selected_ref.current = selected

    def _handle(event) -> bool:
        if not focus or not items:
            return False
        # ★ E8（items 动态缩小越界防护）：内部选中索引钳制到合法范围——items
        #   在挂载后缩小（异步候选刷新）时 selected_ref.current 可能越界，
        #   enter 分支 ``items[selected_ref.current]`` 越界被 router 吞掉。
        #   钳制后同步 state（ref 与 state 一致，选中高亮不消失）。
        cur = _clamp_index(selected_ref.current, len(items))
        if cur != selected_ref.current:
            selected_ref.current = cur
            set_selected(cur)
        if event.kind == "escape" and on_cancel is not None:
            # ★ 弹窗取消协议（方案B）：Esc 时调用 onCancel 并消费事件（返回
            #   True 阻断旧路径 Esc 中断语义——弹窗激活期间由协议接管取消）。
            _call(on_cancel, items[cur])
            return True
        nav = None
        if event.kind == "arrow_up":
            nav = "up"
        elif event.kind == "arrow_down":
            nav = "down"
        else:
            nav = _is_vim_nav(event)
        moved = False
        new = cur
        if nav == "up":
            # ★ P3（review）：已在首项时按上键不移动——无效移动返回 False
            #   （不消费，放行父级；与 ListView/Menu 对齐）。
            if cur > 0:
                new = cur - 1
                moved = True
        elif nav == "down":
            # ★ P3（review）：已在末项时按下键不移动——无效移动返回 False。
            if cur < len(items) - 1:
                new = cur + 1
                moved = True
        elif nav == "first":
            if cur != 0:
                new = 0
                moved = True
        elif nav == "last":
            if cur != len(items) - 1:
                new = len(items) - 1
                moved = True
        if moved:
            selected_ref.current = new
            set_selected(new)
            if on_highlight is not None:
                _call(on_highlight, new)
            return True
        if event.kind == "enter":
            # ★ 方案B：onSelect 未提供时 enter 放行（返回 False 不消费——
            #   CompletionPopup 补全弹窗 Enter 确认由 InputDispatcher 旧路径
            #   接管）；提供时消费并回调（UserSelectPopup 单选协议）。
            if on_select is not None:
                _call(on_select, items[cur])
                return True
            return False
        if consume_all:
            # ★ 弹窗模式：其他按键一律消费（阻断输入框）；Ctrl+C 放行
            #   （可中断工具执行——UserSelectPopup 既有语义）。
            if event.kind == "char" and event.char == "\x03":
                return False
            return True
        return False

    use_input(_handle, focus)

    # ★ P2（review）：渲染期同样钳制——items 收缩后到下一次按键前的帧内
    #   selected state 仍越界，若不钳制则 `idx == selected` 恒 False、无行
    #   高亮（瞬态视觉缺陷）。钳制仅用于高亮/窗口计算，不改 state 本身
    #   （避免渲染期副作用；下一帧事件期钳制会同步 state）。
    selected_shown = _clamp_index(selected, len(items))
    offset, count = _visible_window(selected_shown, len(items), limit)
    rows = []
    # ★ P3（review）：pad 恒按 prefix 显示宽度——修复前 ``if prefix else 2``
    #   在 prefix=""（空串）时仍 pad 2 空格（未选中行比选中行宽 2 错位）。
    pad = " " * wcswidth_simple(prefix)
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_sel = idx == selected_shown
        if render_item is not None:
            # ★ 自定义行渲染（方案B）：弹窗界面（UserSelectPopup 分栏/多行/
            #   高亮视觉）经 renderItem 完全自定义行元素——命中高亮由调用方
            #   自行表达（isSelected 传入）。异常降级默认行（防御）。
            try:
                child = render_item(item, idx, is_sel)
            except Exception:
                child = h(TEXT, {"children": prefix + item["label"], "style": highlight_style if is_sel else None})
            if isinstance(child, Element):
                cp = dict(child.props)
                cp.setdefault("key", f"item-{idx}")
                rows.append(Element(child.type, cp, child.children))
            elif child is None:
                rows.append(h(TEXT, {"children": "", "key": f"item-{idx}"}))
            else:
                rows.append(h(TEXT, {"children": str(child), "key": f"item-{idx}"}))
            continue
        line_prefix = prefix if is_sel else pad
        style = highlight_style if is_sel else None
        rows.append(
            h(TEXT, {"children": line_prefix + item["label"], "style": style, "key": f"item-{idx}"})
        )
    # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
    return h(Column, None, rows)


__all__ = ["SelectInput"]
