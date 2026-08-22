"""radio — RadioList 单选列表控件（React Ink 生态 ink-radio-list 等价物）。

与 SelectInput 的差异：
  - 视觉：RadioList 每行前置**单选指示符** ``◉ ``（选中）/``○ ``（未选中），
    SelectInput 用 ``> `` 前缀 + 高亮样式；
  - 交互：up/down 移动、space/enter 选中（React Ink radio 语义）；
  - 返回值：``onSelect(item)``（item 为 ``{"label", "value"}`` dict）。

基于 ``use_input`` + ``use_state``（同批连续按键经 ref 镜像正确累积），
``focus=False`` 时不参与输入路由（与 SelectInput/MultiSelect 契约一致）。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Column
# ★ P3（review 2026-08-22）：导入路径从 ``widgets.interactive`` 门面改为
#   直接 ``._interactive_common``（与 _select_input/_multi_select 一致，
#   减少门面链依赖层级）。
from ._interactive_common import _normalize_items, _clamp_index, _visible_window, _call

_logger = logging.getLogger(__name__)

__all__ = ["RadioList"]

#: 单选指示符（几何符号单宽，wcswidth_simple 宽度 1——安全对齐）
_CHECKED = "\u25c9 "    # ◉ 实心圆点（选中）
_UNCHECKED = "\u25cb "  # ○ 空心圆点（未选中）


def RadioList(props: dict) -> Element:
    """React Ink ``ink-radio-list`` 等价物：单选列表控件。

    Props:
        items: list[str] 或 list[{"label": str, "value": Any}]。
        onSelect: ``(item) -> None``——选中（space/enter）时回调，item 为
            ``{"label", "value"}`` dict。
        focus: 是否参与输入路由（默认 True）。
        initialIndex: 初始选中下标（默认 0）。
        limit: 可见 item 数（超出滚动窗口；默认 None 全部显示）。
        highlightStyle: 选中行文本样式（默认 ``Style(fg=6)`` cyan）。
        checkedPrefix/uncheckedPrefix: 选中/未选中指示符
            （默认 ``"◉ "`` / ``"○ "``）。

    行为：
      - up/down 移动选中；space/enter 确认（onSelect）；
      - 选中行前置实心圆点，未选中行前置空心圆点（同宽对齐）。

    Returns:
        Column 元素（纵向堆叠的 item 行）。
    """
    items = _normalize_items(props.get("items", []))
    on_select = props.get("onSelect")
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
    # ★ P3（review）：highlightStyle 改 ``is not None`` 判断——修复前 ``or``
    #   把显式空 Style()（falsy）当默认替换。
    highlight_style_prop = props.get("highlightStyle")
    highlight_style = highlight_style_prop if highlight_style_prop is not None else Style(fg=6)
    checked_prefix = str(props.get("checkedPrefix", _CHECKED))
    unchecked_prefix = str(props.get("uncheckedPrefix", _UNCHECKED))
    # 指示符对齐宽度（选中/未选中前缀等宽；自定义前缀时取较宽者）
    prefix_w = max(wcswidth_simple(checked_prefix), wcswidth_simple(unchecked_prefix))

    selected, set_selected = use_state(initial_index)
    # ★ ref 镜像（同批连续按键修复）：与 SelectInput 同语义——handler 闭包
    #   捕获渲染期 state，同批按键之间无重渲染，ref 保存最新值。
    selected_ref = use_ref(selected)
    selected_ref.current = selected
    # ★ 滚动窗口 offset（2026-08-19，跟随光标滚动——与 SelectInput/MultiSelect
    #   同语义）：选项多于可见行数时 ↑/↓ 选中在窗口内逐行移动、越过窗口
    #   边界后窗口才滚动（能移动到未显示的行）。
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
        cur = _clamp_index(selected_ref.current, len(items))
        if cur != selected_ref.current:
            selected_ref.current = cur
            set_selected(cur)
        if event.kind == "arrow_up":
            # ★ P3（review）：已在首项时按上键不移动——无效移动返回 False
            #   （不消费，放行父级；与 SelectInput/MultiSelect 对齐）。
            if cur <= 0:
                return False
            new = cur - 1
            selected_ref.current = new
            set_selected(new)
            # ★ 跟随光标滚动（2026-08-19）：越过窗口上边界时窗口上滚贴顶。
            _scroll_follow(new)
            return True
        if event.kind == "arrow_down":
            # ★ P3（review）：已在末项时按下键不移动——无效移动返回 False。
            if cur >= len(items) - 1:
                return False
            new = cur + 1
            selected_ref.current = new
            set_selected(new)
            # ★ 跟随光标滚动（2026-08-19）：越过窗口下边界时窗口下滚贴底。
            _scroll_follow(new)
            return True
        # ★ P3（review）：``event.kind == "space"`` 为死分支（InputParser 从不
        #   产生 kind=="space"，空格为 ``kind=="char", char==" "``）——删除。
        if event.kind == "enter" or (
            event.kind == "char" and event.char == " " and len(event.char) == 1
        ):
            if on_select is None:
                # ★ P3（review 2026-08-22）：on_select 未注册时放行（与
                #   _select_input 对齐），勿阻断父级 Enter/空格分发。
                return False
            _call(on_select, items[cur])
            return True
        return False

    use_input(_handle, focus)

    selected_shown = _clamp_index(selected, len(items))
    # ★ 跟随光标滚动窗口（2026-08-19）：传当前 offset_ref（事件期推进后的
    #   即时值）——光标在窗口内窗口不动，越过边界才滚动。
    offset, count = _visible_window(selected_shown, len(items), limit, offset_ref.current)
    rows = []
    for i in range(count):
        idx = offset + i
        item = items[idx]
        is_sel = idx == selected_shown
        prefix = checked_prefix if is_sel else unchecked_prefix
        # 指示符等宽对齐（自定义前缀宽度不同时补空格——布局稳定不跳动）
        pad = " " * max(0, prefix_w - wcswidth_simple(prefix))
        style = highlight_style if is_sel else None
        rows.append(
            h(TEXT, {
                "key": f"radio-{idx}",
                "children": prefix + pad + item["label"],
                "style": style,
            })
        )
    return h(Column, None, rows)
