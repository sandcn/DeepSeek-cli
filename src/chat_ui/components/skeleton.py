"""Skeleton 组件 — React Ink 风格骨架屏/加载占位符组件。

提供文本/圆形/矩形三种占位变体，用于内容加载时的占位展示。

使用示例:
    skeleton = Skeleton(variant="text", lines=3)
    print(skeleton.render())
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


class Skeleton(TuiComponent):
    """React Ink Skeleton 组件 — 骨架屏/加载占位符。

    Props:
        variant: str — 占位变体 ("text" / "circle" / "rect")
        lines: int — text 变体的行数（仅 text 有效），默认 3
        width: int — 每行字符宽度，默认 20（text 和 rect 有效）
        height: int — 行数/高度，仅 text 有效且等于 lines
        animated: bool — 是否渲染为闪烁效果（通过 dim 样式模拟），默认 True
        children: list[TuiComponent] — 子组件列表
    """

    VALID_VARIANTS = ("text", "circle", "rect")

    def __init__(
        self,
        variant: str = "text",
        lines: int = 3,
        width: int = 20,
        height: int | None = None,
        animated: bool = True,
        children: list[TuiComponent] | None = None,
    ):
        super().__init__(children=children)
        self._variant = variant if variant in self.VALID_VARIANTS else "text"
        self._lines = max(1, lines)
        self._width = max(4, width)
        self._height = height if height is not None else lines
        self._animated = animated

    @property
    def key(self) -> str:
        return "skeleton"

    def update(self, props: dict) -> bool:
        changed = False
        if "variant" in props:
            new_v = props["variant"] if props["variant"] in self.VALID_VARIANTS else "text"
            if new_v != self._variant:
                self._variant = new_v
                changed = True
        if "lines" in props:
            new_l = max(1, props["lines"])
            if new_l != self._lines:
                self._lines = new_l
                changed = True
        if "width" in props:
            new_w = max(4, props["width"])
            if new_w != self._width:
                self._width = new_w
                changed = True
        if "animated" in props and props["animated"] != self._animated:
            self._animated = props["animated"]
            changed = True
        return changed

    def _render_text(self) -> str | StyledText:
        """渲染文本骨架：多行占位符行。"""
        lines = []
        for i in range(self._lines):
            # 最后一行更短，模拟自然文本布局
            line_len = self._width
            if i == self._lines - 1 and self._lines > 1:
                line_len = max(4, self._width * 3 // 4)
            lines.append("\u2500" * line_len)  # ──
        text = "\n".join(lines)
        return StyledText(text, dim=self._animated)

    def _render_circle(self) -> str | StyledText:
        """渲染圆形骨架：单个圆形占位符。"""
        return StyledText("\u25CB", dim=self._animated)  # ○

    def _render_rect(self) -> str | StyledText:
        """渲染矩形骨架：矩形占位块。"""
        text = "\u258C" * self._width  # ▌
        return StyledText(text, dim=self._animated)

    def render(self) -> str | StyledText:
        if self._variant == "circle":
            return self._render_circle()
        elif self._variant == "rect":
            return self._render_rect()
        return self._render_text()

    def render_vnode(self) -> VNode:
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="skeleton",
            key=self.key,
            props={
                "text": str(rendered) if rendered else "",
                "variant": self._variant,
                "lines": self._lines,
            },
        )
