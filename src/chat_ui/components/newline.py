"""Newline 组件 — React Ink 风格换行符组件。

提供 <Newline count={N}> 组件，插入指定数量的换行符。
在终端输出中直接渲染换行，在 VNode 树中声明换行语义。

使用示例:
    newline = Newline(count=3)
    print(repr(newline.render()))  # '\n\n\n'
"""

from __future__ import annotations

from .base import TuiComponent
from ..vdom.vnode import VNode


class Newline(TuiComponent):
    """React Ink Newline 组件 — 插入换行符。

    Props:
        count: int — 换行数，默认 1
    """

    def __init__(self, count: int = 1, children=None):
        super().__init__(children=children)
        self._count = max(1, count)

    @property
    def key(self) -> str:
        return "newline"

    def update(self, props: dict) -> bool:
        if "count" in props and props["count"] != self._count:
            self._count = max(1, int(props["count"]))
            return True
        return False

    def render(self) -> str:
        return "\n" * self._count

    def render_vnode(self) -> VNode:
        return VNode(type="newline", key=self.key, props={"count": self._count})
