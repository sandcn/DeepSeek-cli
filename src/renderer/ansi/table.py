"""表格渲染 — wcswidth 对齐 + 框线字符 → AnsiLine（无 Rich）。

利用块解析器已产出的 TABLE token（meta: rows + alignments）。
"""

from __future__ import annotations

from src.renderer._utils import cjk_display_width as wcswidth_simple
from .style import Style
from .helpers import AnsiLine
from .inline import render_inline

_STYLE_HEADER = Style(fg=45, bold=True)
_STYLE_BORDER = Style(fg=237)
_STYLE_CELL = Style(fg=252)


def _cell_widths(rows: list[list[str]], aligns: list[str]) -> list[int]:
    """计算每列显示宽度。"""
    ncols = max((len(r) for r in rows), default=0)
    widths = [0] * ncols
    for row in rows:
        for i in range(min(ncols, len(row))):
            w = wcswidth_simple(row[i])
            if w > widths[i]:
                widths[i] = w
    return widths


def _pad_cell(text: str, width: int, align: str) -> str:
    """按对齐填充单元格。"""
    w = wcswidth_simple(text)
    pad = width - w
    if pad <= 0:
        return text
    if align == "right":
        return " " * pad + text
    if align == "center":
        left = pad // 2
        return " " * left + text + " " * (pad - left)
    return text + " " * pad


def _render_row(cells: list[str], widths: list[int], aligns: list[str],
                style: Style | None) -> AnsiLine:
    """渲染数据行：内容填充到列宽，两侧各留 1 空格 → 总宽 width+2 匹配边框。"""
    line = AnsiLine.of("│", _STYLE_BORDER)
    for i in range(len(widths)):
        text = cells[i] if i < len(cells) else ""
        align = aligns[i] if i < len(aligns) else "left"
        padded = _pad_cell(text, widths[i], align)
        cell = " " + padded + " "  # 两侧留白 → 宽度 width+2，与 _border_line 对齐
        if style is not None:
            for run in render_inline(cell, style):
                line.append_run(run)
        else:
            line.append(cell)
        line.append("│", _STYLE_BORDER)
    return line


def _border_line(left: str, mid: str, right: str, widths: list[int]) -> AnsiLine:
    line = AnsiLine.of(left, _STYLE_BORDER)
    for i, w in enumerate(widths):
        line.append("\u2500" * (w + 2), _STYLE_BORDER)
        if i < len(widths) - 1:
            line.append(mid, _STYLE_BORDER)
    line.append(right, _STYLE_BORDER)
    return line


def render_table(token) -> list[AnsiLine]:
    """渲染 TABLE token 为框线表格。"""
    rows = list(token.meta.get("rows", []))
    aligns = list(token.meta.get("alignments", []))
    if not rows:
        return [AnsiLine.of("")]
    widths = _cell_widths(rows, aligns)
    out: list[AnsiLine] = []
    out.append(_border_line("\u250c", "\u252c", "\u2510", widths))
    # 表头
    out.append(_render_row(rows[0], widths, aligns, _STYLE_HEADER))
    out.append(_border_line("\u251c", "\u253c", "\u2524", widths))
    for row in rows[1:]:
        out.append(_render_row(row, widths, aligns, _STYLE_CELL))
    out.append(_border_line("\u2514", "\u2534", "\u2518", widths))
    return out


__all__ = ["render_table"]
