from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Center — 居中对齐容器
# ═══════════════════════════════════════════════════════════


class Center(Widget):
    """居中对齐容器控件。

    将子控件在容器中水平和垂直居中。

    Args:
        child: 子控件。
        axis: 居中对齐轴，"both" / "horizontal" / "vertical"，默认 "both"。
    """

    def __init__(
        self,
        child: Widget,
        axis: str = "both",
        key: str | None = None,
    ) -> None:
        super().__init__(props={"axis": axis}, key=key)
        self._children_source: list[Widget] = [child]
        self._renders_children = True

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表（始终为单元素列表）。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """将子控件居中渲染。"""
        if buffer.is_empty():
            return
        child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
        if child is None:
            return
        axis = self._props.get("axis", "both")

        # 渲染子控件到临时 buffer
        tmp = RenderBuffer(buffer.width, buffer.height)
        try:
            child.render(tmp)
        except Exception as e:
            _logger.debug("Center: child.render failed: %s", e)
            return

        child_str = tmp.render()
        if not child_str:
            return
        child_lines = child_str.split("\n")
        child_w = max((len(l) for l in child_lines), default=0)
        child_h = len(child_lines)

        # 计算偏移
        x_offset = 0
        y_offset = 0

        if axis in ("both", "horizontal"):
            x_offset = max(0, (buffer.width - child_w) // 2)
        if axis in ("both", "vertical"):
            y_offset = max(0, (buffer.height - child_h) // 2)

        # 合并到父缓冲区
        for i, line in enumerate(child_lines):
            dst_y = y_offset + i
            if 0 <= dst_y < buffer.height:
                buffer.write(x_offset, dst_y, line)

    def __repr__(self) -> str:
        return f"Center(axis={self._props.get('axis')})"
