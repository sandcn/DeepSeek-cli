"""Breadcrumbs 组件 — React Ink 风格面包屑导航。

提供 <Breadcrumbs items={["Home", "Docs", "API"]}> 组件。
历史项使用 dim 样式，当前项使用 bold 或自定义颜色，
各段以分隔符拼接。

使用示例:
    bc = Breadcrumbs(items=["Home", "Docs", "API"])
    print(bc.render())  # 输出 "Home ▸ Docs ▸ API" (Home/Docs dim, API bold)
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Breadcrumbs(TuiComponent):
    """React Ink Breadcrumbs 组件 — 面包屑路径导航。

    属性:
        items: 路径段列表，最后一项为当前项。
        separator: 分隔符，默认 " ▸ "（U+25B8 右三角）。
        current_color: 当前项颜色，None 时使用 bold 样式。
        dim_history: 是否对历史项（非最后项）使用 dim 样式。
    """

    def __init__(
        self,
        items: list[str] | None = None,
        separator: str = " \u25b8 ",
        current_color: str | None = None,
        dim_history: bool = True,
        children: list[TuiComponent] | None = None,
    ) -> None:
        """初始化 Breadcrumbs 组件。

        Args:
            items: 路径段列表，最后一项为当前项。
            separator: 分隔符字符串，默认 " ▸ "。
            current_color: 当前项 ANSI 颜色名，None 时用 bold。
            dim_history: 历史项是否 dim，默认 True。
            children: 子组件列表（继承自 TuiComponent）。
        """
        super().__init__(children=children)
        self._items: list[str] = list(items) if items else []
        self._separator: str = separator
        self._current_color: str | None = current_color
        self._dim_history: bool = dim_history

    @property
    def key(self) -> str:
        return "breadcrumbs"

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Args:
            props: 可能包含 'items'、'separator'、'current_color'、
                   'dim_history' 键的字典。

        Returns:
            True 如果任何属性发生变化。
        """
        changed = False
        if "items" in props:
            new_items = list(props["items"])
            if new_items != self._items:
                self._items = new_items
                changed = True
        if "separator" in props:
            self._separator = props["separator"]
            changed = True
        if "current_color" in props:
            self._current_color = props["current_color"]
            changed = True
        if "dim_history" in props:
            self._dim_history = props["dim_history"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染面包屑路径。

        - 空 items → 返回 ""
        - 单元素 → 直接返回该元素文本
        - 多元素 → 使用 StyledText.assemble() 拼接各段：
          历史项 dim（当 dim_history=True），分隔符 dim，
          当前项 bold（或 current_color）

        Returns:
            样式化的面包屑文本，或空字符串。
        """
        if not self._items:
            return ""

        if len(self._items) == 1:
            return self._items[0]

        # 多元素：逐段拼接
        segments: list[str | tuple[str, str]] = []
        for i, item in enumerate(self._items):
            is_last = i == len(self._items) - 1
            if is_last:
                style = self._current_color if self._current_color else "bold"
                segments.append((item, style))
            else:
                if self._dim_history:
                    segments.append((item, "dim"))
                else:
                    segments.append(item)
                segments.append((self._separator, "dim"))

        return StyledText.assemble(*segments)

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。"""
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="breadcrumbs",
            key=self.key,
            props={
                "items": self._items,
                "text": str(rendered) if rendered else "",
                "separator": self._separator,
                "current_color": self._current_color,
                "dim_history": self._dim_history,
            },
        )
