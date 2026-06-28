"""Divider 组件 — React Ink 风格分割线。

提供 <Divider title="章节" /> 组件，渲染填满终端宽度的分割线。
带 title 时文字居中嵌入分割线（如 ─── 标题 ───），
不带 title 时为纯分割线。

使用示例:
    divider = Divider()
    print(divider.render())  # 输出 "──────────────────..."

    divider = Divider(title="章节一", color="cyan", bold=True)
    print(divider.render())  # 输出 "──────── 章节一 ────────"（青色加粗）
"""

from __future__ import annotations

import shutil
import unicodedata
from typing import TYPE_CHECKING

from .base import TuiComponent
from ..infrastructure.styled import StyledText

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


def _visual_width(text: str) -> int:
    """计算文本在终端中的视觉宽度（CJK 宽字符计 2 列，其余计 1 列）。

    Args:
        text: 待计算的文本。

    Returns:
        视觉列宽。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


class Divider(TuiComponent):
    """React Ink Divider 组件 — 填满终端宽度的分割线。

    属性:
        title: 居中显示在分割线中间的文字，None 时为纯分割线。
        color: ANSI 颜色名，应用于分割线字符。
        dim: 是否应用 dim 样式。
        bold: 是否应用 bold 样式。
    """

    def __init__(self, title: str | None = None, color: str | None = None,
                 dim: bool = False, bold: bool = False,
                 children: list[TuiComponent] | None = None) -> None:
        super().__init__(children=children)
        self._title: str | None = title
        self._color: str | None = color
        self._dim: bool = dim
        self._bold: bool = bold

    @property
    def key(self) -> str:
        return "divider"

    def update(self, props: dict) -> bool:
        """接收新 props，判断是否需要重渲染。

        Args:
            props: 可能包含 'title'、'color'、'dim'、'bold' 键的字典。

        Returns:
            True 如果任一属性发生变化。
        """
        changed = False
        if "title" in props:
            new_title = props["title"]
            if new_title != self._title:
                self._title = new_title
                changed = True
        if "color" in props:
            new_color = props["color"]
            if new_color != self._color:
                self._color = new_color
                changed = True
        if "dim" in props:
            new_dim = bool(props["dim"])
            if new_dim != self._dim:
                self._dim = new_dim
                changed = True
        if "bold" in props:
            new_bold = bool(props["bold"])
            if new_bold != self._bold:
                self._bold = new_bold
                changed = True
        return changed

    def render(self) -> str | StyledText:
        """渲染分割线。

        获取终端宽度，若 title 存在则构建左右分割线将 title 居中嵌入，
        否则返回填满终端宽度的纯分割线。
        当 color/dim/bold 有值时使用 StyledText 包裹。

        Returns:
            分割线字符串或样式化文本。
        """
        try:
            width = shutil.get_terminal_size().columns
        except Exception:
            width = 80
        if width <= 0:
            width = 80

        if self._title:
            title_w = _visual_width(self._title)
            # 左右各留 1 空格分隔
            available = width - title_w - 2
            left_cnt = max(0, available // 2)
            right_cnt = max(0, available - left_cnt)

            left_part = "─" * left_cnt
            right_part = "─" * right_cnt
            result = f"{left_part} {self._title} {right_part}"
        else:
            result = "─" * width

        if self._color or self._dim or self._bold:
            return StyledText(
                result,
                fg=self._color,
                dim=self._dim,
                bold=self._bold,
            )
        return result

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。"""
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="divider",
            key=self.key,
            props={
                "title": self._title or "",
                "color": self._color or "",
                "dim": self._dim,
                "bold": self._bold,
                "text": str(rendered) if rendered else "",
            },
        )
