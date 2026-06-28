"""Switch 组件 — React Ink 风格开关组件。

开/关状态切换显示。

使用示例:
    sw = Switch(checked=True, label="启用通知")
    print(sw.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Switch(TuiComponent):
    """React Ink Switch 组件 — 开关。

    渲染格式: [ON] label 或 [OFF] label

    Props:
        checked: bool — 是否开启，默认 False
        label: str — 标签文本（可选）
        disabled: bool — 是否禁用，默认 False
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        checked: bool = False,
        label: str = "",
        disabled: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._checked = checked
        self._label = label
        self._disabled = disabled

    @property
    def key(self) -> str:
        return "switch"

    def update(self, props: dict) -> bool:
        changed = False
        if "checked" in props and props["checked"] != self._checked:
            self._checked = props["checked"]
            changed = True
        if "label" in props and props["label"] != self._label:
            self._label = props["label"]
            changed = True
        if "disabled" in props and props["disabled"] != self._disabled:
            self._disabled = props["disabled"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        if self._checked:
            switch_text = StyledText("[ON]", fg="green", bold=True)
        else:
            switch_text = StyledText("[OFF]", dim=True)

        if not self._label:
            if self._disabled:
                return StyledText(str(switch_text), dim=True)
            return switch_text

        label = StyledText(self._label)

        if self._disabled:
            # 禁用时整体 dim
            combined = StyledText.assemble(switch_text, StyledText(" "), label)
            return StyledText(str(combined), dim=True)

        return StyledText.assemble(switch_text, StyledText(" "), label)

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="switch",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "checked": self._checked,
                "disabled": self._disabled,
            },
        )
