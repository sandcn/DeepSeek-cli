"""表格渲染 — wcswidth 对齐 + 框线字符 → AnsiLine（无 Rich）。

利用块解析器已产出的 TABLE token（meta: rows + alignments）。

宽度自适应（更适合命令行显示）：
  - 按终端宽度收缩列宽（表总宽含边框 ≤ width），超宽列经 ``_shrink_widths``
    收缩至内容预算；
  - 单元格内容按列宽换行（``_wrap_runs``，保持样式、不拆宽字符）；
  - 单元格先经 ``render_inline`` 解析行内 markdown（剥离语法）再测量/对齐——
    修复内联格式（``**加粗**``/``code``）下原始文本宽度与渲染后宽度不一致
    导致的边框错位。
"""

from __future__ import annotations

from src.renderer._utils import cjk_display_width as wcswidth_simple
from .style import Style
from .helpers import AnsiLine, Run
from .inline import render_inline

_STYLE_HEADER = Style(fg=45, bold=True)
_STYLE_BORDER = Style(fg=237)
_STYLE_CELL = Style(fg=252)


def _cell_runs(text: str, style) -> list[Run]:
    """单元格文本 → Run 序列（render_inline 解析行内 markdown，剥离语法）。"""
    return render_inline(text, style)


def _cell_widths_runs(rows, style) -> list[int]:
    """计算每列显示宽度（按渲染后文本宽度，样式不影响宽度）。"""
    ncols = max((len(r) for r in rows), default=1)
    widths = [0] * ncols
    for row in rows:
        for i in range(min(ncols, len(row))):
            w = sum(r.width for r in _cell_runs(row[i], style))
            if w > widths[i]:
                widths[i] = w
    return widths


def _shrink_widths(widths: list[int], max_total: int, ncols: int) -> list[int]:
    """按终端宽度收缩列宽（表总宽含边框 ≤ max_total）。

    表总宽 = sum(列宽) + 3*ncols + 1（每列两侧 1 空格 + ``│`` 边框 + 角）。
    收缩最宽列至内容预算 ``max_total - 3*ncols - 1``（保底每列 1）。
    """
    widths = list(widths)
    budget = max_total - 3 * ncols - 1  # 内容总预算
    if budget <= 0:
        return [1] * ncols
    while sum(widths) > budget and min(widths) > 1:
        i = max(range(ncols), key=lambda i: widths[i])
        if widths[i] > 1:
            widths[i] -= 1
        else:
            break
    return widths


def _wrap_runs(runs: list[Run], maxw: int) -> list[list[Run]]:
    """Run 序列按显示宽度换行（保持样式，不拆宽字符）。"""
    if maxw <= 0:
        return [list(runs)] if runs else [[]]
    lines: list[list[Run]] = []
    cur: list[Run] = []
    cur_w = 0
    for run in runs:
        buf = ""
        buf_w = 0
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if cur_w + buf_w + cw > maxw and (cur or buf):
                if buf:
                    cur.append(Run(buf, run.style))
                    buf = ""
                    buf_w = 0
                lines.append(cur)
                cur = []
                cur_w = 0
            buf += ch
            buf_w += cw
        if buf:
            cur.append(Run(buf, run.style))
            cur_w += buf_w
    if cur:
        lines.append(cur)
    return lines if lines else [[]]


def _pad_runs(runs: list[Run], width: int, align: str, style) -> list[Run]:
    """将 Run 序列按对齐填充至 width（补空格）。"""
    w = sum(r.width for r in runs)
    pad = width - w
    if pad <= 0:
        return runs
    if align == "right":
        return [Run(" " * pad, style)] + list(runs)
    if align == "center":
        left = pad // 2
        return [Run(" " * left, style)] + list(runs) + [Run(" " * (pad - left), style)]
    return list(runs) + [Run(" " * pad, style)]


def _render_row_runs(cells, widths, aligns, style) -> list[AnsiLine]:
    """渲染数据行：单元格 runs 按列宽 wrap → 多行（每行带 ``│`` 边框）。"""
    ncols = len(widths)
    wrapped = [
        _wrap_runs(_cell_runs(cells[i] if i < len(cells) else "", style), widths[i])
        for i in range(ncols)
    ]
    max_lines = max((len(w) for w in wrapped), default=1)
    out: list[AnsiLine] = []
    for li in range(max_lines):
        line = AnsiLine.of("\u2502", _STYLE_BORDER)
        for i in range(ncols):
            runs = wrapped[i][li] if li < len(wrapped[i]) else []
            align = aligns[i] if i < len(aligns) else "left"
            padded = _pad_runs(runs, widths[i], align, style)
            line.append(" ", None)
            for run in padded:
                line.append_run(run)
            line.append(" ", None)
            line.append("\u2502", _STYLE_BORDER)
        out.append(line)
    return out


def _border_line(left: str, mid: str, right: str, widths: list[int]) -> AnsiLine:
    line = AnsiLine.of(left, _STYLE_BORDER)
    for i, w in enumerate(widths):
        line.append("\u2500" * (w + 2), _STYLE_BORDER)
        if i < len(widths) - 1:
            line.append(mid, _STYLE_BORDER)
    line.append(right, _STYLE_BORDER)
    return line


def render_table(token, width: int = 0) -> list[AnsiLine]:
    """渲染 TABLE token 为框线表格（宽度自适应：收缩列宽 + 单元格换行）。"""
    rows = list(token.meta.get("rows", []))
    aligns = list(token.meta.get("alignments", []))
    if not rows:
        return [AnsiLine.of("")]
    ncols = max((len(r) for r in rows), default=1)
    widths = _cell_widths_runs(rows, _STYLE_CELL)
    if width and width > 0:
        widths = _shrink_widths(widths, width, ncols)
    out: list[AnsiLine] = []
    out.append(_border_line("\u250c", "\u252c", "\u2510", widths))
    out.extend(_render_row_runs(rows[0], widths, aligns, _STYLE_HEADER))
    out.append(_border_line("\u251c", "\u253c", "\u2524", widths))
    for row in rows[1:]:
        out.extend(_render_row_runs(row, widths, aligns, _STYLE_CELL))
    out.append(_border_line("\u2514", "\u2534", "\u2518", widths))
    return out


__all__ = ["render_table"]
