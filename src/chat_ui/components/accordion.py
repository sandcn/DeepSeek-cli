"""Accordion 组件 — React Ink 风格手风琴/折叠面板组件。

多面板折叠容器，每次仅展开一个面板。

使用示例:
    acc = Accordion(
        items=[("面板1", Text("内容1")), ("面板2", Text("内容2"))],
        default_open=0,
    )
    print(acc.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Accordion(TuiComponent):
    """React Ink Accordion 组件 — 手风琴/折叠面板。

    多个可折叠面板，每次最多展开一个。
    展开面板: ▼ title + 内容
    折叠面板: ▶ title

    Props:
        items: list[tuple[str, Any]] — [(title, content), ...]
            content 可以是 TuiComponent 或 str
        default_open: int — 默认展开的面板索引（-1 表示全折叠）
        bold_title: bool — 标题是否加粗，默认 True
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        items: list[tuple[str, Any]] | None = None,
        default_open: int = -1,
        bold_title: bool = True,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._items = list(items) if items else []
        self._open_index = default_open if 0 <= default_open < len(self._items) else -1
        self._bold_title = bold_title

    @property
    def key(self) -> str:
        return "accordion"

    @property
    def open_index(self) -> int:
        """当前展开的面板索引，-1 表示全折叠。"""
        return self._open_index

    @open_index.setter
    def open_index(self, value: int) -> None:
        """设置展开的面板索引，-1 表示全折叠。"""
        if value < -1 or value >= len(self._items):
            return
        self._open_index = value

    def update(self, props: dict) -> bool:
        changed = False
        if "items" in props and props["items"] != self._items:
            self._items = list(props["items"]) if props["items"] else []
            # 重置 open_index
            self._open_index = -1
            changed = True
        if "default_open" in props:
            new_idx = props["default_open"]
            clamped = new_idx if 0 <= new_idx < len(self._items) else -1
            if clamped != self._open_index:
                self._open_index = clamped
                changed = True
        if "bold_title" in props and props["bold_title"] != self._bold_title:
            self._bold_title = props["bold_title"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        if not self._items:
            return ""

        parts: list[str | StyledText] = []

        for i, (title, content) in enumerate(self._items):
            is_open = i == self._open_index

            if is_open:
                # 展开: ▼ title (加粗)
                parts.append(StyledText(f"\u25BC {title}", bold=self._bold_title))  # ▼
                # 内容缩进
                if isinstance(content, TuiComponent):
                    content_output = content.render()
                    if isinstance(content_output, str):
                        parts.append(StyledText(f"  {content_output}"))
                    else:
                        content_str = str(content_output)
                        for line in content_str.split("\n"):
                            parts.append(StyledText(f"  {line}"))
                else:
                    parts.append(StyledText(f"  {str(content)}"))
            else:
                # 折叠: ▶ title (dim)
                parts.append(StyledText(f"\u25B6 {title}", dim=True))  # ▶

        children_output = self.render_children()
        if children_output:
            if isinstance(children_output, str):
                parts.append(StyledText(f"\n{children_output}"))
            else:
                parts.append(StyledText("\n"))
                parts.append(children_output)

        if not parts:
            return ""

        return StyledText.assemble(*parts) if len(parts) > 1 else parts[0]

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="accordion",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "open_index": self._open_index,
                "item_count": len(self._items),
            },
        )
