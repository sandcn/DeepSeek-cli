"""checkbox — React Ink 风格复选控件（Checkbox）。

单行复选开关（``[x] label`` / ``[ ] label`` 样式，对齐常见终端 checkbox）：

    - space/enter 切换勾选态并触发 ``onChange(checked)``；
    - 受控（``checked`` prop）+ 内部初始态（``defaultChecked``）双模式；
    - ``label`` 可为 str 或 Element（自定义标签）。

依赖约束：仅依赖 element / output / core.style / hooks / widgets.layout
（Layer 0/1），无父包依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..hooks import use_state, use_input, use_ref
from ..widgets.layout import Row
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_call 原本地定义——收敛至
#   _widget_common 单一真源。
from ._widget_common import _call

__all__ = ["Checkbox"]


#: 默认勾选样式（绿 fg=41）
_CHECKBOX_CHECKED = Style(fg=41, bold=True)

#: 默认未勾选样式（dim 灰 244）
_CHECKBOX_UNCHECKED = Style(fg=244)


def Checkbox(props: dict) -> Element:
    """React Ink 风格复选控件。

    Props:
        checked: 受控勾选态（None=非受控，用内部状态）。
        defaultChecked: 内部初始勾选态（默认 False）。
        onChange: ``(checked: bool) -> None``——切换回调。
        focus: 是否参与输入路由（默认 True）。
        label: 标签（str 或 Element；None 时仅渲染方框）。
        labelStyle: 标签样式（默认 None）。
        checkedStyle: 勾选态样式（默认 ``Style(fg=41, bold=True)``）。
        uncheckedStyle: 未勾选态样式（默认 ``Style(fg=244)``）。

    行为：
      - space（或空格 char）/ enter 切换勾选态并触发 ``onChange``；
      - 其余按键放行（不消费）。

    Returns:
        Row 元素（方框 + 标签，横向排列）。
    """
    checked_prop = props.get("checked")
    controlled = checked_prop is not None
    default_checked = bool(props.get("defaultChecked", False))
    onChange = props.get("onChange")
    focus = bool(props.get("focus", True))
    label = props.get("label")
    label_style = props.get("labelStyle")
    # ★ P3（review）：checkedStyle 用 ``or`` 判断——显式空 Style()（falsy）
    #   被默认样式替换。改 ``is not None`` 判断：显式传入空样式按原样保留。
    checked_style_prop = props.get("checkedStyle")
    checked_style = checked_style_prop if checked_style_prop is not None else _CHECKBOX_CHECKED
    # ★ P3（review）：uncheckedStyle 同样改 ``is not None`` 判断（与
    #   checkedStyle 一致）——修复前 ``or`` 把显式空 Style()（falsy）当默认替换。
    unchecked_style_prop = props.get("uncheckedStyle")
    unchecked_style = unchecked_style_prop if unchecked_style_prop is not None else _CHECKBOX_UNCHECKED

    internal_checked, set_internal_checked = use_state(default_checked)
    checked = bool(checked_prop) if controlled else internal_checked
    # ref 镜像（同批连续按键）：handler 读 ref
    checked_ref = use_ref(checked)
    checked_ref.current = checked

    def _handle(event) -> bool:
        if not focus:
            return False
        # ★ P3（review）：``event.kind == "space"`` 为死分支（InputParser 从不
        #   产生 kind=="space"，空格为 ``kind=="char", char==" "``）——删除。
        if (event.kind == "char" and event.char == " ") or event.kind == "enter":
            new_value = not checked_ref.current
            checked_ref.current = new_value
            if not controlled:
                set_internal_checked(new_value)
            _call(onChange, new_value)
            return True
        return False

    use_input(_handle, focus)

    mark = "[x]" if checked else "[ ]"
    mark_style = checked_style if checked else unchecked_style
    children = [h(TEXT, {"children": mark, "style": mark_style, "height": 1})]
    if isinstance(label, Element):
        children.append(label)
    elif label is not None:
        children.append(h(TEXT, {
            "children": " " + str(label), "style": label_style, "height": 1,
        }))
    # ★ 标准布局：Row 横向排列方框 + 标签
    return h(Row, {"height": 1}, children)


__all__ = ["Checkbox"]
