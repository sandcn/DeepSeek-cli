from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Grid — 网格布局
# ═══════════════════════════════════════════════════════════


class Grid(Widget):
    """网格布局控件。

    将子控件按二维网格排列。每个子控件占据一个网格单元格，
    所有单元格宽度一致（等分容器宽度），高度自适应。

    Args:
        children: 二维子控件列表，每行一个 list[Widget]。
        cols: 列数。默认 None 时根据子控件自动计算（取最大行宽）。
        spacing: 单元格间距（字符数），默认 1。
        align: 水平对齐方式，"left" / "center" / "right"，默认 "left"。
        valign: 垂直对齐方式，"top" / "center" / "bottom"，默认 "top"。
    """

    def __init__(
        self,
        children: list[list[Widget]] | None = None,
        cols: int | None = None,
        spacing: int = 1,
        align: str = "left",
        valign: str = "top",
        key: str | None = None,
    ) -> None:
        super().__init__(props={
            "cols": cols, "spacing": spacing,
            "align": align, "valign": valign,
        }, key=key)
        self._children_source: list[list[Widget]] = children or []
        self._flat_children: list[Widget] = []
        for row in self._children_source:
            self._flat_children.extend(row)
        self._renders_children = True

    def compose(self) -> list[Widget]:
        """返回扁平化后的子控件列表。"""
        return self._flat_children

    def render(self, buffer: RenderBuffer) -> None:
        """网格排列渲染所有子控件。"""
        children = self._children_source
        if not children:
            return
        sp = self._props.get("spacing", 1)
        al = self._props.get("align", "left")
        val = self._props.get("valign", "top")

        # 确定列数
        max_cols = max(len(row) for row in children) if children else 0
        cols = self._props.get("cols") or max_cols

        if cols <= 0:
            return

        # 计算每列宽度（等分容器宽度，减去间距）
        total_spacing = sp * (cols - 1) if cols > 1 else 0
        col_width = max(1, (buffer.width - total_spacing) // cols) if cols > 0 else buffer.width

        y = 0
        for row_idx, row in enumerate(children):
            # 先渲染行中所有单元格到临时 buffer，确定行高
            row_heights: list[int] = []
            row_buffers: list[RenderBuffer] = []

            for cell in row:
                cell_buf = RenderBuffer(col_width, buffer.height - y)
                try:
                    cell.render(cell_buf)
                except Exception as e:
                    _logger.debug("Grid: cell.render failed: %s", e)
                    cell_buf = RenderBuffer(col_width, 1)
                cell_str = cell_buf.render()
                cell_h = max(1, len(cell_str.split("\n")) if cell_str else 1)
                row_heights.append(cell_h)
                row_buffers.append(cell_buf)

            # 该行所有单元格的最大高度
            row_height = max(row_heights) if row_heights else 1

            # 按水平位置合并到父缓冲区
            x = 0
            for col_idx in range(cols):
                if x >= buffer.width:
                    break
                if col_idx < len(row_buffers):
                    cb = row_buffers[col_idx]
                    ch = row_heights[col_idx]
                    # 垂直对齐
                    y_offset = 0
                    if val == "center":
                        y_offset = max(0, (row_height - ch) // 2)
                    elif val == "bottom":
                        y_offset = max(0, row_height - ch)
                    # 水平对齐
                    if al == "center":
                        cell_w = col_width
                        tmp_str = cb.render()
                        tmp_lines = tmp_str.split("\n") if tmp_str else [""]
                        x_offset = max(0, (cell_w - max((len(l) for l in tmp_lines), default=0)) // 2)
                        for i, line in enumerate(tmp_lines):
                            dst_y = y + y_offset + i
                            if dst_y < buffer.height:
                                buffer.write(x + x_offset, dst_y, line)
                    elif al == "right":
                        cell_w = col_width
                        tmp_str = cb.render()
                        tmp_lines = tmp_str.split("\n") if tmp_str else [""]
                        max_line_w = max((len(l) for l in tmp_lines), default=0)
                        x_offset = max(0, cell_w - max_line_w)
                        for i, line in enumerate(tmp_lines):
                            dst_y = y + y_offset + i
                            if dst_y < buffer.height:
                                buffer.write(x + x_offset, dst_y, line)
                    else:
                        buffer.merge(cb, x, y + y_offset)
                x += col_width + sp

            y += row_height
            if y >= buffer.height:
                break

    def __repr__(self) -> str:
        flat = self._flat_children if self._flat_children else []
        return f"Grid({len(flat)} cells)"
