"""Tabs 组件 — React Ink 风格标签页组件。

提供 <Tabs tabs=[...] active_id="tab1"> 组件，用于水平标签 + 内容区显示。

使用示例:
    tabs = Tabs(
        items=[("tab1", "标签1", Text("内容1")), ("tab2", "标签2", Text("内容2"))],
        active_id="tab1",
    )
    print(tabs.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Tabs(TuiComponent):
    """React Ink Tabs 组件 — 标签页。

    水平标签栏 + 当前标签内容区。
    活跃标签加粗+蓝色，非活跃 dim 暗色。

    Props:
        items: list[tuple[str, str, Any]] — [(id, title, content), ...]
        active_id: str — 当前活跃标签 ID
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        items: list[tuple[str, str, Any]] | None = None,
        active_id: str = "",
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._items = list(items) if items else []
        self._active_id = active_id

    @property
    def key(self) -> str:
        return "tabs"

    def update(self, props: dict) -> bool:
        changed = False
        if "items" in props and props["items"] != self._items:
            self._items = list(props["items"]) if props["items"] else []
            changed = True
        if "active_id" in props and props["active_id"] != self._active_id:
            self._active_id = props["active_id"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染标签页。

        标签栏: [tab1] [tab2] [tab3]
        活跃标签蓝色加粗，非活跃 dim。
        内容区: 仅渲染活跃标签的内容。
        """
        if not self._items:
            return ""

        parts: list[str | StyledText] = []

        # 标签栏
        tab_parts: list[str | StyledText] = []
        for i, (tid, title, _) in enumerate(self._items):
            is_active = tid == self._active_id
            sep = " " if i == 0 else "  "
            if is_active:
                tab_parts.append(StyledText(f"{sep}[{title}]", fg="blue", bold=True))
            else:
                tab_parts.append(StyledText(f"{sep} {title} ", dim=True))

        tab_bar = StyledText.assemble(*tab_parts) if tab_parts else StyledText("")
        parts.append(tab_bar)
        parts.append(StyledText(""))  # 空行分隔

        # 内容区 — 渲染活跃标签的内容
        active_content = None
        for tid, title, content in self._items:
            if tid == self._active_id:
                active_content = content
                break

        if active_content is not None:
            if isinstance(active_content, TuiComponent):
                parts.append(active_content.render())
            else:
                parts.append(StyledText(str(active_content)))

        children_output = self.render_children()
        if children_output:
            parts.append(StyledText(str(children_output)))

        if len(parts) == 1:
            return parts[0]
        return StyledText.assemble(*parts)

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="tabs",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "active_id": self._active_id,
                "tab_count": len(self._items),
            },
        )
