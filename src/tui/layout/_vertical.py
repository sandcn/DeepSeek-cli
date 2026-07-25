from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Vertical — 垂直布局
# ═══════════════════════════════════════════════════════════


class Vertical(Widget):
    """垂直布局控件。

    将多个子控件从上到下垂直排列。
    支持间距（spacing）和水平对齐（align）。

    Args:
        children: 子控件列表。
        spacing: 子控件之间的间距（行数），默认 0。
        align: 水平对齐方式，"left" / "center" / "right"，默认 "left"。
    """

    def __init__(
        self,
        children: list,
        spacing: int = 0,
        align: str = "left",
        max_height: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(props={
            "spacing": spacing,
            "align": align,
            "max_height": max_height,
        }, key=key)
        self._children_source: list[Widget] = list(children)
        self._renders_children = True

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """垂直排列渲染所有子控件。

        每个子控件先渲染到临时缓冲区，再按垂直位置合并到父缓冲区。
        支持 left/center/right 水平对齐。
        支持通过 max_height 限制最大高度（超出截断）。
        """
        sp = self._props.get("spacing", 0)
        al = self._props.get("align", "left")
        max_height = self._props.get("max_height")
        children = self._children if self._children else self._children_source
        # 有效高度受 buffer.height 和 max_height 共同约束
        effective_h = buffer.height
        if max_height is not None:
            effective_h = min(effective_h, max_height)
        y = 0
        for child in children:
            if y >= effective_h:
                break
            # 创建临时缓冲区渲染子控件
            tmp = RenderBuffer(buffer.width, effective_h - y)
            try:
                child.render(tmp)
            except Exception as e:
                _logger.debug("Vertical: child.render failed: %s", e)
                tmp = RenderBuffer(buffer.width, 1)
            # 获取渲染内容
            child_str = tmp.render()
            child_lines = child_str.split("\n") if child_str else [""]
            child_h = max(1, len(child_lines))
            # 确保不超出父缓冲区
            child_h = min(child_h, effective_h - y)
            # 将渲染内容写入父缓冲区（支持对齐）
            for i, line in enumerate(child_lines):
                row = y + i
                if row >= effective_h or i >= child_h:
                    break
                stripped = line.rstrip()
                if not stripped:
                    continue
                if al == "center":
                    x = max(0, (buffer.width - len(stripped)) // 2)
                elif al == "right":
                    x = max(0, buffer.width - len(stripped))
                else:  # left
                    x = 0
                buffer.write(x, row, stripped)
            y += child_h + sp

    def __repr__(self) -> str:
        children_len = len(self._children) if self._children else len(self._children_source)
        return f"Vertical({children_len} children)"
