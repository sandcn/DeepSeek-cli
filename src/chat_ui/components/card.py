"""Card 组件 — React Ink 风格卡片容器组件。

提供 <Card title="标题" footer="页脚"> 容器，带边框和区域划分。

使用示例:
    card = Card(title="卡片", children=[Text("内容")], footer="页脚信息")
    print(card.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText
from ..infrastructure.ansi import ANSI_RESET

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Card(TuiComponent):
    """React Ink Card 组件 — 带标题/内容/页脚的卡片容器。

    Props:
        title: str — 卡片标题（可选）
        footer: str — 卡片页脚文本（可选，dim 样式）
        border_style: str — 边框样式，可选 solid/dashed/none，默认 none
        padding: int — 内容区缩进空格数，默认 2
        width: int | None — 卡片宽度（字符数），默认 None 自动
        bold_title: bool — 标题是否加粗，默认 True
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        title: str = "",
        footer: str = "",
        border_style: str = "none",
        padding: int = 2,
        width: int | None = None,
        bold_title: bool = True,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._title = title
        self._footer = footer
        self._border_style = border_style if border_style in ("solid", "dashed", "none") else "none"
        self._padding = padding
        self._width = width
        self._bold_title = bold_title

    @property
    def key(self) -> str:
        return "card"

    def update(self, props: dict) -> bool:
        changed = False
        if "title" in props and props["title"] != self._title:
            self._title = props["title"]
            changed = True
        if "footer" in props and props["footer"] != self._footer:
            self._footer = props["footer"]
            changed = True
        if "border_style" in props:
            new_v = props["border_style"] if props["border_style"] in ("solid", "dashed", "none") else "none"
            if new_v != self._border_style:
                self._border_style = new_v
                changed = True
        if "padding" in props and props["padding"] != self._padding:
            self._padding = props["padding"]
            changed = True
        if "bold_title" in props and props["bold_title"] != self._bold_title:
            self._bold_title = props["bold_title"]
            changed = True
        return changed

    def _render_border_line(self, char: str) -> str:
        """渲染单行边框。"""
        w = self._width if self._width else 40
        return f" {char * (w - 2)} " if w > 2 else ""

    def render(self) -> str | StyledText:
        parts: list[str | StyledText] = []

        # 标题行
        if self._title:
            parts.append(StyledText(f" {self._title}", bold=self._bold_title))

        # 内容区（children）
        children_output = self.render_children()
        if children_output:
            pad = " " * self._padding
            if isinstance(children_output, str):
                for line in children_output.split("\n"):
                    parts.append(StyledText(f"{pad}{line}"))
            else:
                parts.append(StyledText(f"{pad}"))
                parts.append(children_output)
            parts.append(StyledText(""))

        # 页脚
        if self._footer:
            parts.append(StyledText(f" {self._footer}", dim=True))

        if not parts:
            return ""

        result = StyledText.assemble(*parts) if len(parts) > 1 else parts[0]
        return result

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="card",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "title": self._title,
            },
        )
