"""Table — 对齐表格控件（可选表头/边框，React Ink 风格）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——表格
独立成模块（公共辅助经 ``_display_common`` 共享）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from ..element import TEXT, Element, h
from ..widgets.layout import Column
from ._display_common import _color

#: 边框字符元组：(左上, 右上, 左下, 右下, 横, 竖, 左T, 右T, 下T, 上T, 交叉)
_BORDER_TABLE: dict[str, tuple[str, str, str, str, str, str, str, str, str, str, str]] = {
    "single": ("┌", "┐", "└", "┘", "─", "│", "├", "┤", "┬", "┴", "┼"),
    "round": ("╭", "╮", "╰", "╯", "─", "│", "├", "┤", "┬", "┴", "┼"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃", "┣", "┫", "┳", "┻", "╋"),
    "classic": ("+", "+", "+", "+", "-", "|", "+", "+", "+", "+", "+"),
    "double": ("╔", "╗", "╚", "╝", "═", "║", "╠", "╣", "╦", "╩", "╬"),
}


def _table_border_row(chars: tuple, cell_w: list[int], left, mid, right) -> str:
    """构建表格边框行（顶/分隔/底共用）。"""
    parts = []
    for i, w in enumerate(cell_w):
        parts.append(chars[4] * w)
        if i < len(cell_w) - 1:
            parts.append(mid)
    return left + "".join(parts) + right


def Table(props: dict) -> Element:
    """React Ink ``<Table>`` 等价物：对齐表格控件。

    Props:
        data: 数据行（list of list/tuple；单元格 str() 化）。
        columns: 表头行（list of str；None 表示无表头）。
        padding: 单元格左右内边距（默认 1）。
        border: 边框风格（None=无边框对齐 | "single"/"round"/"bold"/
            "classic"/"double"）。
        headerColor: 表头前景色（默认 ``"cyan"``）。
        headerStyle: 表头完整样式（优先于 headerColor）。
        cellStyle: 数据单元格样式（默认 None）。
        borderColor: 边框颜色（颜色名/int；默认暗青 23）。

    Returns:
        BOX 元素（纵向堆叠的表格行）。
    """
    data = props.get("data", []) or []
    columns = props.get("columns")
    try:
        padding = max(0, int(props.get("padding", 1)))
    except (TypeError, ValueError, OverflowError):
        padding = 1
    border = props.get("border")
    if border is True:
        border = "single"
    border = str(border) if border else None
    header_style = props.get("headerStyle")
    if header_style is None:
        header_style = Style(fg=_color(props.get("headerColor", "cyan")), bold=True)
    cell_style = props.get("cellStyle")
    border_style = Style(fg=_color(props.get("borderColor"), 23))

    rows: list[list[str]] = []
    if columns is not None:
        rows.append([str(c) for c in columns])
    for row in data:
        rows.append([str(c) for c in row])
    if not rows:
        # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
        return h(Column, None, [])

    ncols = max(len(r) for r in rows)
    widths = [0] * ncols
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], wcswidth_simple(cell))

    has_header = columns is not None

    # ── 无边框：纯对齐文本 ──
    if not border:
        lines = []
        for ri, r in enumerate(rows):
            cells = []
            for i in range(ncols):
                cell = r[i] if i < len(r) else ""
                cells.append(cell + " " * (widths[i] - wcswidth_simple(cell)))
            text = (" " * padding).join(cells).rstrip()
            if has_header and ri == 0:
                lines.append(h(TEXT, {"children": text, "style": header_style}))
            else:
                lines.append(h(TEXT, {"children": text, "style": cell_style}))
        # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
        return h(Column, None, lines)

    # ── 带边框 ──
    chars = _BORDER_TABLE.get(border, _BORDER_TABLE["single"])
    tl, tr, bl, br, hz, vt, ml, mr, mt, mb, cr = chars
    cell_w = [w + padding * 2 for w in widths]
    top = _table_border_row(chars, cell_w, tl, mt, tr)
    sep = _table_border_row(chars, cell_w, ml, cr, mr)
    bottom = _table_border_row(chars, cell_w, bl, mb, br)

    lines = [h(TEXT, {"children": top, "style": border_style})]
    for ri, r in enumerate(rows):
        cells = []
        for i in range(ncols):
            cell = r[i] if i < len(r) else ""
            cells.append(" " * padding + cell + " " * (widths[i] - wcswidth_simple(cell)) + " " * padding)
        row_text = vt + vt.join(cells) + vt
        if has_header and ri == 0:
            lines.append(h(TEXT, {"children": row_text, "style": header_style}))
            lines.append(h(TEXT, {"children": sep, "style": border_style}))
        else:
            lines.append(h(TEXT, {"children": row_text, "style": cell_style}))
    lines.append(h(TEXT, {"children": bottom, "style": border_style}))
    # ★ 阶段2（标准布局容器重构）：column BOX → Column（语义化门面，输出等价）。
    return h(Column, None, lines)


__all__ = ["Table"]
