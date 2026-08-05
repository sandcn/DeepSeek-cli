"""focus — React Ink 风格焦点管理组件（FocusGroup / Key）。

React Ink 生态中多个可聚焦组件（如多个 ``TextInput``/``SelectInput``）之间
用 ``Tab``/``Shift+Tab`` 切换焦点。本模块提供：

  - ``FocusGroup``：容器组件，管理一组 ``Key`` 的焦点索引（经 props 注入
    焦点索引/总数/切换回调）；
  - ``Key``：声明可聚焦区域，内部 ``use_input`` 消费 Tab（前进）/Shift+Tab
    （后退，用 ``arrow_left`` 模拟——React Ink 无原生 Shift+Tab 事件，常见
    实现绑定 ``tab`` 与反向键），并把焦点切换请求转发给 FocusGroup；仅当前
    激活的 Key 参与输入路由（``use_input(handler, active)``），子组件经
    ``focus`` prop 注入激活状态。

用法::

    from src.tui.ink.widgets import FocusGroup, Key
    from src.tui.ink.widgets import TextInput

    def Comp(props):
        return h(FocusGroup, None, [
            h(Key, None, h(TextInput, {"placeholder": "A"})),
            h(Key, None, h(TextInput, {"placeholder": "B"})),
        ])

    # Tab 在两个输入框之间切换焦点（激活的 Key 内子组件 focus=True）。

依赖约束：仅依赖 element / output / hooks / widgets.layout（Layer 0/1），
无父包依赖。
"""

from __future__ import annotations

from ..element import TEXT, Element, h
from ..hooks import use_state, use_input
from ..widgets.layout import Column
# ★ 公共纯辅助收敛（2026-08-05 架构优化）：_children/_call 原本地定义（与
#   layout/_panel/checkbox 等逐字重复）——收敛至 _widget_common 单一真源。
from ._widget_common import _children, _call

__all__ = ["FocusGroup", "Key"]


def FocusGroup(props: dict) -> Element:
    """React Ink 风格焦点组容器。

    遍历子节点，为每个 ``Key`` 子组件注入焦点上下文 prop：
      - ``_focus_index``：在组内的序号；
      - ``_focus_total``：组内 Key 总数；
      - ``_focus_active``：当前是否激活；
      - ``_focus_set``：切换激活序号回调。

    自身渲染为 Column（标准布局）；非 Key 子节点原样透传（不注入）。

    Props:
        initialIndex: 初始激活序号（默认 0）。
        children: 子组件（通常为 ``Key``）。

    Returns:
        Column 元素（子节点 + 焦点上下文注入）。
    """
    children = _children(props)
    try:
        initial = max(0, int(props.get("initialIndex", 0)))
    except (TypeError, ValueError, OverflowError):
        initial = 0
    active, set_active = use_state(initial)
    n_keys = sum(
        1 for c in children if isinstance(c, Element) and c.type is Key
    )
    if n_keys == 0:
        return h(Column, None, children)
    # 激活序号钳制到 [0, n_keys)
    if active >= n_keys:
        active = 0
    wrapped: list = []
    key_idx = 0
    for child in children:
        if isinstance(child, Element) and child.type is Key:
            cp = dict(child.props)
            cp["_focus_index"] = key_idx
            cp["_focus_total"] = n_keys
            cp["_focus_active"] = (key_idx == active)
            cp["_focus_set"] = set_active
            wrapped.append(Element(child.type, cp, child.children))
            key_idx += 1
        else:
            wrapped.append(child)
    return h(Column, props.get("height"), wrapped)


def Key(props: dict) -> Element:
    """React Ink ``<Key>`` 等价物：声明可聚焦区域。

    Props:
        children: 内容（通常为单个交互组件——``focus`` prop 会被激活状态
            覆盖注入）。
        _focus_index/_focus_total/_focus_active/_focus_set: FocusGroup 注入
            （缺省视为未受管的独立 Key——不参与焦点切换）。

    行为：
      - 当前激活（``_focus_active``）时注册 ``use_input``（消费 Tab 前进 /
        arrow_left 后退——React Ink 常见实现；仅激活 Key 参与路由）；
      - 子组件（单 Element）经 ``focus`` prop 注入激活状态——未激活 Key 内
        子组件 ``focus=False``（不参与输入路由，实现多区域焦点互斥）。

    Returns:
        子组件（Element 注入 focus 后原样返回）。
    """
    children = _children(props)
    index = props.get("_focus_index")
    total = props.get("_focus_total")
    active = bool(props.get("_focus_active", False))
    set_active = props.get("_focus_set")
    managed = index is not None and total is not None

    def _handle(event) -> bool:
        # 仅受管且激活的 Key 处理焦点切换
        if not managed or not active or set_active is None:
            return False
        if event.kind == "tab" or (event.kind == "char" and event.char == "\t"):
            _call(set_active, (index + 1) % total)
            return True
        if event.kind == "arrow_left":
            _call(set_active, (index - 1) % total)
            return True
        return False

    use_input(_handle, active)

    # 子组件注入 focus（激活状态）
    if children:
        first = children[0]
        if isinstance(first, Element):
            cp = dict(first.props)
            cp["focus"] = active
            return Element(first.type, cp, first.children)
        return h(TEXT, {"children": str(first), "height": 1})
    return h(TEXT, {"children": " ", "height": 1})


__all__ = ["FocusGroup", "Key"]
