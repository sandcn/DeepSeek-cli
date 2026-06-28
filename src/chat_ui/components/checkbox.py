"""Checkbox 组件 — React Ink 风格复选框组件。

勾选/未勾选状态显示。

使用示例:
    cb = Checkbox(checked=True, label="同意条款")
    print(cb.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Checkbox(TuiComponent):
    """React Ink Checkbox 组件 — 复选框。

    渲染格式: [✓] label（选中） 或 [ ] label（未选中）

    Props:
        checked: bool — 是否选中，默认 False
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
        return "checkbox"

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
            box = StyledText("[\u2713]", fg="green", bold=True)  # [✓]
        else:
            box = StyledText("[ ]", dim=True)

        if self._label:
            label = StyledText(f" {self._label}")
            combined = StyledText.assemble(box, label)
        else:
            combined = box

        if self._disabled:
            if isinstance(combined, StyledText):
                return StyledText(str(combined), dim=True)
            return combined

        return combined

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="checkbox",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "checked": self._checked,
                "disabled": self._disabled,
            },
        )
