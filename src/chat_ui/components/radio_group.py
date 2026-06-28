"""RadioGroup 组件 — React Ink 风格单选框组组件。

选项组，单选，支持自定义标签。

使用示例:
    rg = RadioGroup(options=[("opt1", "选项1"), ("opt2", "选项2")], selected="opt1")
    print(rg.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class RadioGroup(TuiComponent):
    """React Ink RadioGroup 组件 — 单选框组。

    渲染格式:
    (●) 选项1  (选中项: fg=blue, bold)
    (○) 选项2  (未选项: dim)

    Props:
        options: list[tuple[str, str]] — [(value, label), ...]
        selected: str — 当前选中值
        inline: bool — 是否水平排列，默认 False（垂直）
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        options: list[tuple[str, str]] | None = None,
        selected: str = "",
        inline: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._options = list(options) if options else []
        self._selected = selected
        self._inline = inline

    @property
    def key(self) -> str:
        return "radio_group"

    def update(self, props: dict) -> bool:
        changed = False
        if "options" in props and props["options"] != self._options:
            self._options = list(props["options"]) if props["options"] else []
            changed = True
        if "selected" in props and props["selected"] != self._selected:
            self._selected = props["selected"]
            changed = True
        if "inline" in props and props["inline"] != self._inline:
            self._inline = props["inline"]
            changed = True
        return changed

    def _render_option(self, value: str, label: str) -> StyledText:
        """渲染单个选项。"""
        is_selected = value == self._selected
        if is_selected:
            return StyledText(f"(\u25CF) {label}", fg="blue", bold=True)  # (●)
        else:
            return StyledText(f"(\u25CB) {label}", dim=True)  # (○)

    def render(self) -> str | StyledText:
        if not self._options:
            return ""

        parts: list[str | StyledText] = []
        for i, (value, label) in enumerate(self._options):
            opt = self._render_option(value, label)
            if i > 0:
                if self._inline:
                    parts.append(StyledText("  "))
                else:
                    parts.append(StyledText("\n"))
            parts.append(opt)

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
            type="radio_group",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "selected": self._selected,
                "option_count": len(self._options),
            },
        )
