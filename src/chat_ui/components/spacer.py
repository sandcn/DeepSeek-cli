"""Spacer 组件 — React Ink 风格弹性空白填充。

提供 <Spacer> 组件，在 FlexLayout 中消耗剩余空间。
独立使用时渲染一个空格，在 flex 容器中由布局引擎扩展宽度。

使用示例:
    spacer = Spacer()
    print(repr(spacer.render()))  # ' '
"""

from __future__ import annotations

from .base import TuiComponent
from ..vdom.vnode import VNode


class Spacer(TuiComponent):
    """React Ink Spacer 组件 — 弹性空白填充。

    在 FlexLayout 中消耗剩余空间。独立使用时渲染一个空格。
    """

    @property
    def key(self) -> str:
        return "spacer"

    def render(self) -> str:
        return " "  # 至少占一个字符，在 flex 容器中由布局引擎扩展

    def render_vnode(self) -> VNode:
        return VNode(type="spacer", key=self.key, props={})
