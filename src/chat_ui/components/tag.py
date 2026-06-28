"""Tag 组件 — React Ink 风格标签组件。

轻量标签，用于分类标记，风格不同于 Badge（状态徽章）。
Tag 更简洁，仅文本 + 颜色前缀标记。

预设 blue/green/red/yellow/purple/gray 映射颜色。

使用示例:
    tag = Tag(text="Python", preset="blue")
    print(tag.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


_PRESET_COLORS: dict[str, str] = {
    "blue": "blue",
    "green": "green",
    "red": "red",
    "yellow": "yellow",
    "purple": "magenta",
    "gray": "white",
}


class Tag(TuiComponent):
    """React Ink Tag 组件 — 轻量标签。

    Props:
        text: str — 标签文本
        preset: str — 预设颜色 (blue/green/red/yellow/purple/gray)
        bold: bool — 是否加粗
        dim: bool — 是否暗色
        children: list[TuiComponent] — 子组件列表
    """

    def __init__(
        self,
        text: str = "",
        preset: str = "gray",
        bold: bool = False,
        dim: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._text = text
        self._preset = preset if preset in _PRESET_COLORS else "gray"
        self._bold = bold
        self._dim = dim

    @property
    def key(self) -> str:
        return "tag"

    def update(self, props: dict) -> bool:
        changed = False
        if "text" in props and props["text"] != self._text:
            self._text = props["text"]
            changed = True
        if "preset" in props:
            new_p = props["preset"] if props["preset"] in _PRESET_COLORS else "gray"
            if new_p != self._preset:
                self._preset = new_p
                changed = True
        if "bold" in props and props["bold"] != self._bold:
            self._bold = props["bold"]
            changed = True
        if "dim" in props and props["dim"] != self._dim:
            self._dim = props["dim"]
            changed = True
        return changed

    def render(self) -> str | StyledText:
        if not self._text:
            return ""
        color = _PRESET_COLORS.get(self._preset, "white")
        # 渲染格式: • text
        return StyledText(
            f"\u2022 {self._text}",
            fg=color,
            bold=self._bold,
            dim=self._dim,
        )

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="tag",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "preset": self._preset,
            },
        )
