"""tabs — React Ink 风格标签页组件（Tabs）。

标签行 + 内容区：左右键切换标签。标签行渲染为
Row（水平排列），内容区渲染为 Column（垂直堆叠）。

标签形态：:

    tabs = [
        {"label": "对话", "key": "chat"},
        {"label": "工具", "key": "tools"},
    ]

- ``activeKey`` 受控激活标签（外部状态）或 ``defaultActiveKey``（内部状态）；
- ``onChange(tab, key)`` 切换回调；
- ``renderContent`` ``(tab, index) -> Element`` 内容渲染函数（缺省不渲染内容）；
- ``showContent`` 是否渲染内容区（默认 True）。

依赖约束：仅依赖 element / output / core.style / hooks / widgets.layout
（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Row, Column
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_clamp_index 原本地定义——收敛
#   至 _widget_common 单一真源。
from ._widget_common import _clamp_index

_logger = logging.getLogger(__name__)

__all__ = ["Tabs"]


#: 激活标签样式（亮青 fg=45 + bold）
_TAB_ACTIVE = Style(fg=45, bold=True)

#: 非激活标签样式（dim 灰 244）
_TAB_INACTIVE = Style(fg=244)

#: 激活指示符（几何符号单宽）
_TAB_ACTIVE_MARK = "● "
_TAB_INACTIVE_MARK = "○ "


def _normalize_tabs(tabs) -> list[dict]:
    """规范化 tabs 为 ``{"label": str, "key": str}``。"""
    if tabs is None:
        return []
    if not hasattr(tabs, "__iter__"):
        return []
    out: list[dict] = []
    for t in tabs:
        if isinstance(t, dict):
            label = str(t.get("label", t.get("key", "")))
            key = str(t.get("key", label))
        else:
            label = str(t)
            key = label
        out.append({"label": label, "key": key})
    return out


def Tabs(props: dict) -> Element:
    """React Ink 风格标签页控件。

    Props:
        tabs: 标签列表（str 或 dict：label/key）。
        activeKey: 受控激活标签 key（指定时优先于内部状态）。
        defaultActiveKey: 初始激活标签 key（内部状态）。
        onChange: ``(tab, key, index) -> None``——切换回调。
        focus: 是否参与输入路由（默认 True）。
        activeStyle/inactiveStyle: 激活/非激活标签样式。
        showMarks: 是否显示 ● / ○ 指示符（默认 True）。
        gap: 标签间距（默认 2）。
        renderContent: ``(tab, index) -> Element`` 内容渲染（缺省不渲染）。
        showContent: 是否渲染内容区（默认 True）。

    Returns:
        Column 元素（标签行 + 内容区）。
    """
    tabs = _normalize_tabs(props.get("tabs", []))
    active_key = props.get("activeKey")
    default_key = props.get("defaultActiveKey")
    onChange = props.get("onChange")
    focus = bool(props.get("focus", True))
    # ★ P3（review 2026-08-18）：``is not None`` 判断——修复前 ``or`` 把显式
    #   空 Style()（falsy）替换为默认样式（与其他控件契约一致；
    #   activeStyle/inactiveStyle 两处）。
    active_style_prop = props.get("activeStyle")
    active_style = active_style_prop if active_style_prop is not None else _TAB_ACTIVE
    inactive_style_prop = props.get("inactiveStyle")
    inactive_style = inactive_style_prop if inactive_style_prop is not None else _TAB_INACTIVE
    show_marks = bool(props.get("showMarks", True))
    try:
        gap = max(0, int(props.get("gap", 2)))
    except (TypeError, ValueError, OverflowError):
        gap = 2
    render_content = props.get("renderContent")
    show_content = bool(props.get("showContent", True))

    # ★ P2（review）：受控/非受控分支条件性调用 use_state（违反 hook 顺序
    #   规则）——修复为无条件调用 use_state，再按 active_key 是否受控决定
    #   是否用内部值。受控时 set_internal_idx 仍可用（事件期不调用）。
    internal_idx, set_internal_idx = use_state(
        next(
            (i for i, t in enumerate(tabs) if t["key"] == str(default_key)), 0,
        ) if default_key is not None else 0,
    )
    if active_key is not None:
        active_idx = next(
            (i for i, t in enumerate(tabs) if t["key"] == str(active_key)), 0,
        )
    else:
        active_idx = internal_idx
    active_idx = _clamp_index(active_idx, len(tabs))
    # ref 镜像（受控变化 + 同批按键）
    active_ref = use_ref(active_idx)
    active_ref.current = active_idx

    def _handle(event) -> bool:
        if not focus or not tabs:
            return False
        cur = _clamp_index(active_ref.current, len(tabs))
        if event.kind in ("arrow_left", "arrow_up"):
            new = (cur - 1) % len(tabs) if len(tabs) > 1 else cur
        elif event.kind in ("arrow_right", "arrow_down"):
            new = (cur + 1) % len(tabs) if len(tabs) > 1 else cur
        # ★ P3（review）：``event.kind == "space"`` 为死分支（InputParser 从不
        #   产生 kind=="space"，空格为 ``kind=="char", char==" "``）——删除。
        elif event.kind == "enter" or (event.kind == "char" and event.char == " "):
            # ★ P2（review）：space/enter **不切换**（仅箭头键切换，与模块
            #   docstring 语义修正一致），但**消费事件**——修复前 ``new = cur``
            #   使 ``new != cur`` 恒 False → 落入 return False 放行父级（事件
            #   未消费，与「enter/space 可切换」表述矛盾，且造成父级重复响应）。
            return True
        else:
            return False
        if new != cur and active_key is None and len(tabs) > 1:
            active_ref.current = new
            set_internal_idx(new)
            if onChange is not None:
                try:
                    onChange(tabs[new], tabs[new]["key"], new)
                except Exception:
                    # ★ P2（review）：回调异常静默吞（无日志）——补 debug 日志
                    #   便于排查（修复前 ``except Exception: pass``）。
                    _logger.debug("Tabs onChange 回调异常", exc_info=True)
            # ★ P2（review）：内部 idx 已切换（非受控）→ **消费事件**——修复前
            #   onChange is None 时 return False（事件未消费），造成「标签已
            #   切换 + 焦点也后退」双重响应。与有 onChange 时一致：内部状态
            #   已变化即 return True。
            return True
        if new != cur and onChange is not None:
            try:
                onChange(tabs[new], tabs[new]["key"], new)
            except Exception:
                _logger.debug("Tabs onChange 回调异常", exc_info=True)
            return True
        return False

    use_input(_handle, focus)

    if not tabs:
        return h(Column, None, [])

    # 标签行（Row 水平排列）
    tab_children: list = []
    for i, tab in enumerate(tabs):
        is_active = i == active_idx
        style = active_style if is_active else inactive_style
        label = tab["label"]
        if "\n" in label:
            label = label.replace("\n", " ")
        mark = _TAB_ACTIVE_MARK if is_active else _TAB_INACTIVE_MARK
        text = (mark + label) if show_marks else label
        tab_children.append(h(TEXT, {
            "children": text, "style": style, "height": 1,
            "key": f"tab-{i}",
        }))
        if i < len(tabs) - 1 and gap > 0:
            tab_children.append(h(TEXT, {"children": " " * gap, "height": 1}))
    row = h(Row, {"height": 1}, tab_children)
    if not show_content:
        return row
    content_el = render_content(tabs[active_idx], active_idx) if render_content is not None else None
    if content_el is None:
        return h(Column, None, [row])
    return h(Column, None, [row, content_el])


__all__ = ["Tabs"]
