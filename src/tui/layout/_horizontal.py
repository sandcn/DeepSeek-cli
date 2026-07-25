from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Horizontal — 水平布局
# ═══════════════════════════════════════════════════════════


class Horizontal(Widget):
    """水平布局控件。

    将多个子控件从左到右水平排列。
    支持间距（spacing）和垂直对齐（align）。

    Args:
        children: 子控件列表。
        spacing: 子控件之间的间距（列数），默认 1。
        align: 垂直对齐方式，"top" / "center" / "bottom"，默认 "top"。
    """

    def __init__(
        self,
        children: list,
        spacing: int = 1,
        align: str = "top",
        max_width: int | None = None,
        key: str | None = None,
    ) -> None:
        super().__init__(props={
            "spacing": spacing,
            "align": align,
            "max_width": max_width,
        }, key=key)
        self._children_source: list[Widget] = list(children)
        self._renders_children = True

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """水平排列渲染所有子控件。

        每个子控件先渲染到临时缓冲区，再按水平位置合并到父缓冲区。
        支持 top/center/bottom 垂直对齐。
        支持通过 max_width 限制最大宽度（超出截断）。
        """
        sp = self._props.get("spacing", 1)
        al = self._props.get("align", "top")
        max_width = self._props.get("max_width")
        children = self._children if self._children else self._children_source
        # 有效宽度受 buffer.width 和 max_width 共同约束
        effective_w = buffer.width
        if max_width is not None:
            effective_w = min(effective_w, max_width)
        x = 0
        max_h = buffer.height
        for child in children:
            if x >= effective_w:
                break
            # 为子控件创建临时缓冲区
            child_buf = RenderBuffer(effective_w - x, max_h)
            try:
                child.render(child_buf)
            except Exception as e:
                _logger.debug("Horizontal: child.render failed: %s", e)
                child_buf = RenderBuffer(effective_w - x, 1)
            child_str = child_buf.render()
            child_lines = child_str.split("\n") if child_str else [""]
            child_w = max((len(l) for l in child_lines), default=1)
            child_h = len(child_lines)
            # 计算垂直偏移
            y_offset = 0
            if al == "center":
                y_offset = max(0, (max_h - child_h) // 2)
            elif al == "bottom":
                y_offset = max(0, max_h - child_h)
            # 合并到父缓冲区
            for i, line in enumerate(child_lines):
                dst_y = y_offset + i
                if 0 <= dst_y < buffer.height:
                    buffer.write(x, dst_y, line)
            x += child_w + sp

    def __repr__(self) -> str:
        children_len = len(self._children) if self._children else len(self._children_source)
        return f"Horizontal({children_len} children)"
