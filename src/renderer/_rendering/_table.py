"""_rendering._table — 表格渲染：宽度估算 + Rich Table 构建。"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

from rich.text import Text
from rich.table import Table, box

from .._utils import cjk_display_width

from ._blocks import split_by_br


# ═══════════════════════════════════════════════════════════
# 表格宽度估算
# ═══════════════════════════════════════════════════════════

def estimate_table_width(headers: list[str], data_rows: list[list[str]]) -> list[int]:
    """估算表格每列的最小宽度。"""
    col_min_widths = []
    for i, h in enumerate(headers):
        w = cjk_display_width(h)
        for row in data_rows:
            if len(row) != len(headers):
                _logger.warning("estimate_table_width: 行数据列数(%d) 与表头列数(%d) 不匹配", len(row), len(headers))
            cell = row[i] if i < len(row) else ""
            w = max(w, cjk_display_width(cell))
        col_min_widths.append(w)
    return col_min_widths


# ═══════════════════════════════════════════════════════════
# 共享表格构建
# ═══════════════════════════════════════════════════════════

def _render_table_cell(
    cell: str,
    render_inline_fn,
) -> Text:
    """渲染表格单元格内容（含 <br> 换行处理）。"""
    if '<br' in cell:
        parts = split_by_br(cell)
        parts = [p for p in parts if p]
        if not parts:
            return Text("")
        cell_text = Text()
        for pi, part in enumerate(parts):
            if pi > 0:
                cell_text.append("\n")
            cell_text.append_text(render_inline_fn(part.strip()))
        return cell_text
    else:
        return render_inline_fn(cell)


def build_rich_table(
    rows: list[list[str]],
    alignments: list[str] | None,
    render_inline_fn,
    output_width: int,
) -> Table:
    """构建 Rich Table 对象（共享给 terminal.py 和 table.py）。

    Args:
        rows: 行数据（第一行为表头）
        alignments: 各列对齐方式
        render_inline_fn: 内联渲染函数，接收 str → 返回 Rich Text
        output_width: 终端输出宽度

    Returns:
        Rich Table 对象
    """
    if not rows:
        return Table()

    alignments = alignments or []
    headers = rows[0]
    data_rows = rows[1:]
    num_cols = len(headers)
    aligns = (alignments + ['left'] * num_cols)[:num_cols]

    col_min_widths = estimate_table_width(headers, data_rows)
    total_est = sum(col_min_widths) + num_cols * 2 + (num_cols - 1) * 3
    too_wide = total_est > output_width

    table = Table(
        show_header=True,
        header_style="bold",
        box=box.SIMPLE if too_wide else None,
        padding=(0, 1),
        collapse_padding=not too_wide,
    )

    for i, h in enumerate(headers):
        justify = aligns[i] if i < len(aligns) else 'left'
        table.add_column(render_inline_fn(h), justify=justify)

    for row in data_rows:
        padded = (row + [''] * num_cols)[:num_cols]
        rendered = [_render_table_cell(cell, render_inline_fn) for cell in padded]
        table.add_row(*rendered)

    return table
