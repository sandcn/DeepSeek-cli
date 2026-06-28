"""Scrollbar 组件 — React Ink 风格终端滚动条指示器。

提供 <Scrollbar> 组件，使用 Unicode block 字符渲染垂直滚动条，
指示当前滚动位置和内容比例。

使用示例:
    scrollbar = Scrollbar(total=100, current=50, height=5)
    print(scrollbar.render())  # '░\n░\n█\n░\n░'
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TuiComponent

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


# Unicode block 字符
_THUMB_CHAR = "█"   # 滑块（满块）
_TRACK_CHAR = "░"   # 轨道空（浅色）

# 多行分隔符
_SEPARATOR = "\n"


class Scrollbar(TuiComponent):
    """React Ink Scrollbar 组件 — 垂直滚动条指示器。

    使用 Unicode block 字符渲染滚动条：
    - █ (U+2588) — 滑块（当前位置）
    - ░ (U+2591) — 轨道空

    Props:
        total: int — 总行数（内容总高度）。
        current: int — 当前滚动位置（0-based，顶部为 0）。
        height: int — 可见区域高度（滚动条渲染行数）。
    """

    def __init__(self, total: int = 0, current: int = 0, height: int = 10,
                 children=None):
        """初始化 Scrollbar 组件。

        Args:
            total: 内容总行数，自动 clamp 到 ≥1。
            current: 当前行（0-based），自动 clamp 到 [0, total-1]。
            height: 可见行数（滚动条高度），自动 clamp 到 ≥1。
        """
        super().__init__(children=children)
        self._total = max(1, int(total))
        self._current = max(0, min(int(current), self._total - 1))
        self._height = max(1, int(height))

    @property
    def key(self) -> str:
        return "scrollbar"

    def update(self, props: dict) -> bool:
        """接收新 props，对比变化决定是否重渲染。"""
        changed = False
        if "total" in props:
            new_total = max(1, int(props["total"]))
            if new_total != self._total:
                self._total = new_total
                changed = True
        if "current" in props:
            new_current = max(0, min(int(props["current"]), self._total - 1))
            if new_current != self._current:
                self._current = new_current
                changed = True
        if "height" in props:
            new_height = max(1, int(props["height"]))
            if new_height != self._height:
                self._height = new_height
                changed = True
        return changed

    def render(self) -> str:
        """渲染滚动条为多行字符串。

        计算逻辑：
        1. ratio = height / total（可见比例）
        2. thumb_size = max(1, int(height * ratio))（滑块大小，至少 1）
        3. thumb_pos = int((current / total) * height)（滑块起始位置）
        4. thumb_pos 限制在 [0, height - thumb_size] 范围内

        边界条件：
        - total ≤ height 时全部渲染为 ░（内容不超出可见区，无需滚动）
        - total = 0 时自动修正为 1

        Returns:
            以 \\n 分隔的多行滚动条字符串（每行一个字符）。
        """
        total = self._total
        height = self._height
        current = self._current

        # 内容不超出可见区：渲染全部轨道（无滑块）
        if total <= height:
            return _SEPARATOR.join([_TRACK_CHAR] * height)

        # 计算滑块位置和大小
        ratio = height / total
        thumb_size = max(1, int(height * ratio))
        thumb_pos = int((current / total) * height)
        # clamp 滑块位置到有效范围
        thumb_pos = max(0, min(thumb_pos, height - thumb_size))

        chars: list[str] = []
        for i in range(height):
            if thumb_pos <= i < thumb_pos + thumb_size:
                chars.append(_THUMB_CHAR)
            else:
                chars.append(_TRACK_CHAR)
        return _SEPARATOR.join(chars)

    def render_vnode(self) -> VNode:
        """产出 VNode — 声明式渲染的主入口。"""
        from ..vdom.vnode import VNode
        rendered = self.render()
        return VNode(
            type="scrollbar",
            key=self.key,
            props={
                "total": self._total,
                "current": self._current,
                "height": self._height,
                "text": rendered,
            },
        )
