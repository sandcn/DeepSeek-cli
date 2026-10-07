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


def _split_runs_newlines(runs: list[Run]) -> list[list[Run]]:
    """把 Run 序列按换行符拆成多段（每段不含 ``\\n``）。

    ``<br>``（``LineBreakNode``）在单元格内渲染为 ``"\\n"`` run；若原样交给
    ``_wrap_runs``，换行符会被当作宽度 0 的普通字符写进同一行，``AnsiLine``
    输出含裸换行 → 表格框线被撕裂（单元格内容溢出到下一物理行）。此处先按
    换行切分，每段独立测宽/wrap，再由 ``_render_row_runs`` 合并为多行单元格。
    """
    parts: list[list[Run]] = [[]]
    for run in runs:
        text = run.text or ""
        if not text:
            continue
        if "\n" not in text:
            parts[-1].append(run)
            continue
        segs = text.split("\n")
        for i, seg in enumerate(segs):
            if i > 0:
                parts.append([])
            if seg:
                parts[-1].append(Run(seg, run.style))
    # 去掉尾部空段（内容以换行结尾时不留多余空行），至少保留一段
    while len(parts) > 1 and not parts[-1]:
        parts.pop()
    return parts


def _cell_runs_width(runs: list[Run]) -> int:
    """单元格 runs 的显示宽度（含换行时取各段最大宽度）。"""
    parts = _split_runs_newlines(runs)
    if len(parts) == 1:
        return sum(r.width for r in parts[0])
    return max((sum(r.width for r in seg) for seg in parts), default=0)


def _cell_widths_runs(rows, style) -> list[int]:
    """计算每列显示宽度（按渲染后文本宽度，样式不影响宽度）。"""
    ncols = max(map(len, rows), default=1)
    widths = [0] * ncols
    for row in rows:
        for i in range(min(ncols, len(row))):
            w = _cell_runs_width(_cell_runs(row[i], style))
            if w > widths[i]:
                widths[i] = w
    return widths


def _row_cell_widths(row, style) -> list[int]:
    """单行的各单元格显示宽度（增量列宽计算用，避免每帧重算历史行）。"""
    return [_cell_runs_width(_cell_runs(cell, style)) for cell in row]


def _shrink_widths(widths: list[int], max_total: int, ncols: int) -> list[int]:
    """按终端宽度收缩列宽（表总宽含边框 ≤ max_total）。

    表总宽 = sum(列宽) + 3*ncols + 1（每列两侧 1 空格 + ``│`` 边框 + 角）。
    收缩最宽列至内容预算 ``max_total - 3*ncols - 1``（保底每列 1）。
    """
    widths = list(widths)
    budget = max_total - 3 * ncols - 1  # 内容总预算
    if budget <= 0:
        return [1] * ncols
    # ★ 修复（review 方向）：循环守卫用 max(widths) > 1 而非 min(widths) > 1
    #   ——修复前任一列已为 1（如 ✔ 列）即整体停止收缩，宽列无法再降，
    #   超预算表格溢出终端宽度。
    while sum(widths) > budget and max(widths) > 1:
        i = max(range(ncols), key=lambda i: widths[i])
        if widths[i] > 1:
            widths[i] -= 1
        else:
            break
    return widths


def _wrap_runs(runs: list[Run], maxw: int) -> list[list[Run]]:
    """Run 序列按显示宽度换行（保持样式，不拆宽字符）。

    ★ 性能（表格预览热路径）：先累加缓存宽度做**整段快路径**——总宽不超过
    ``maxw`` 时（表格单元格的绝大多数形态：内容短于列宽）直接返回单行，
    免逐字符 ``wcswidth_simple`` 展开。修复前无论是否换行都对每个单元格
    逐字符测宽，列宽变化重建整表时（新数据行改变列宽）开销随总字符数线性
    累积。快路径产出的 runs 与慢路径一致（同样过滤空文本 run：慢路径的空
    run 不产生 buf，故不进入结果行）。
    """
    if maxw <= 0:
        return [list(runs)] if runs else [[]]
    total = 0
    for run in runs:
        total += run.width
    if total <= maxw:
        trimmed = [run for run in runs if run.text]
        return [trimmed] if trimmed else [[]]
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
    """渲染数据行：单元格 runs 按列宽 wrap → 多行（每行带 ``│`` 边框）。

    单元格内 ``<br>``（换行）先按段拆分再逐段 wrap，段与段之间保留为独立的
    单元格行——单元格内容不会带裸换行溢出框线之外。
    """
    ncols = len(widths)
    wrapped: list[list[list[Run]]] = []
    for i in range(ncols):
        runs = _cell_runs(cells[i] if i < len(cells) else "", style)
        col_lines: list[list[Run]] = []
        for seg in _split_runs_newlines(runs):
            col_lines.extend(_wrap_runs(seg, widths[i]))
        if not col_lines:
            col_lines = [[]]
        wrapped.append(col_lines)
    max_lines = max(map(len, wrapped), default=1)
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


class TablePreviewCache:
    """表格预览的行级增量渲染缓存（流式未闭合表格预览专用）。

    表格预览每次 write 都整表重渲染，成本集中在两处：列宽计算对每个单元格做
    行内解析（O(行数×列数)）、框线/对齐对每行重排。本缓存把两步都增量化：

      - 每行单元格宽度（``_header_width`` / ``_data_widths``）——新增行只算
        新行的宽度；
      - 列宽（``_widths``，取各列 max 后按终端宽度收缩）；
      - 表头块（上边框 + 表头 + 中边框，``_head``）与已渲染数据行（``_data``，
        按行独立）——列宽不变时只渲染新增行，历史行对象复用（UI 侧 styled
        缓存因此可命中）；
      - 数据区**头部滑窗复用**：``_preview_table_rows`` 截断会移除旧数据行，
        新数据行前若干行等于缓存数据行后若干行时复用对应渲染行。

    重建条件：列宽变化（新增/移除行改变某列最大宽度、终端宽度变化）、表头
    变化——整体重建（对齐基线变化无法局部复用）。

    行约定：``rows[0]`` 为表头，``rows[1:]`` 为数据行（与 ``_data`` 一一对应）。
    """

    __slots__ = ("_key", "_ncols", "_aligns", "_header", "_header_width",
                 "_head", "_data_src", "_data_widths", "_data", "_bottom",
                 "_widths", "_shrink_key", "_shrink_result")

    _MAX_SLIDE = 8
    """头部滑窗探测的最大行数（预览截断每次仅移除少量旧行）。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """清空缓存（块闭合 / 预览清空 / 类型切换时调用）。"""
        self._key = None
        self._ncols = 0
        self._aligns: tuple = ()
        self._header: list[str] | None = None
        self._header_width: list[int] = []
        self._head: list[AnsiLine] = []
        self._data_src: list[list[str]] = []
        self._data_widths: list[list[int]] = []
        self._data: list[list[AnsiLine]] = []
        self._bottom: list[AnsiLine] = []
        self._widths: list[int] = []
        # 列宽收缩结果缓存（键 = (未收缩列宽, 终端宽度)）——未变化时跳过
        # ``_shrink_widths``（超宽表格该函数为 O(超量×列数)）。
        self._shrink_key: tuple | None = None
        self._shrink_result: list[int] | None = None

    def render(self, key, rows: list[list[str]], aligns: list[str],
               term_width: int = 0) -> list[AnsiLine]:
        """渲染表格预览（复用未变化的列宽与历史行）。

        Args:
            key: 缓存键（区分表格实例；同帧多个表格时切换会重置）。
            rows: 表格行（``rows[0]`` 为表头，其余为数据行）。
            aligns: 列对齐方式。
            term_width: 终端宽度（列宽收缩基准；0 表示不收缩）。
        """
        ncols = max(map(len, rows), default=1) if rows else 1
        aligns_t = tuple(aligns)
        if (key != self._key or ncols != self._ncols
                or aligns_t != self._aligns):
            self.reset()
            self._key = key
            self._ncols = ncols
            self._aligns = aligns_t

        header = list(rows[0]) if rows else []
        data = list(rows[1:]) if rows else []
        if header != self._header:
            # 表头变化（首帧 / 列定义修正）→ 清空数据区缓存
            self._header = header
            self._header_width = (_row_cell_widths(header, _STYLE_HEADER)
                                  if header else [])
            self._head = []
            self._data_src = []
            self._data_widths = []
            self._data = []
        else:
            self._reuse_data(data)

        # 追加新增数据行（源 + 单元格宽度）
        for row in data[len(self._data_src):]:
            self._data_src.append(row)
            self._data_widths.append(_row_cell_widths(row, _STYLE_CELL))

        # 列宽 = max(表头, 各数据行) → 终端宽度收缩（行宽 ≤ ncols，直接 enumerate）
        widths = [0] * ncols
        for i, v in enumerate(self._header_width):
            widths[i] = v
        for rw in self._data_widths:
            for i, v in enumerate(rw):
                if v > widths[i]:
                    widths[i] = v
        if term_width and term_width > 0:
            # ★ 性能：列宽收缩结果缓存——未收缩列宽与终端宽度均未变时跳过
            #   ``_shrink_widths``（逐步削减为 O(超量 × 列数)，超宽表格每帧
            #   重算曾是预览热路径开销）。
            shrink_key = (tuple(widths), term_width)
            if shrink_key != self._shrink_key:
                self._shrink_key = shrink_key
                self._shrink_result = _shrink_widths(widths, term_width, ncols)
            widths = self._shrink_result
        else:
            self._shrink_key = None
            self._shrink_result = None

        aligns_list = list(self._aligns)
        if widths != self._widths or not self._head:
            # 列宽变化 / 首次 → 重建表头块、底边框与全部数据行
            self._widths = widths
            self._head = []
            self._data = []
            self._bottom = []
            if rows:
                self._head.append(_border_line("\u250c", "\u252c", "\u2510", widths))
                self._head.extend(_render_row_runs(
                    rows[0], widths, aligns_list, _STYLE_HEADER))
                self._head.append(_border_line("\u251c", "\u253c", "\u2524", widths))
                self._bottom.append(_border_line("\u2514", "\u2534", "\u2518", widths))
            for row in self._data_src:
                self._data.append(_render_row_runs(
                    row, widths, aligns_list, _STYLE_CELL))
        else:
            # 列宽稳定 → 仅补齐新增数据行
            for row in self._data_src[len(self._data):]:
                self._data.append(_render_row_runs(
                    row, widths, aligns_list, _STYLE_CELL))

        out: list[AnsiLine] = list(self._head)
        for data_lines in self._data:
            out.extend(data_lines)
        out.extend(self._bottom)
        return out

    def _reuse_data(self, data: list[list[str]]) -> None:
        """数据区复用：前缀相同 + 头部滑窗；使 ``_data_src`` 成为 ``data`` 前缀。

        ★ 性能（表格预览热路径）：先走**纯追加 C 级快路径**——流式表格预览
        的常见形态是「已渲染数据行不变、尾部追加新行」（``data`` 以
        ``_data_src`` 为前缀）。修复前逐行 Python 级 ``data[common] ==
        cached[common]`` 循环比较（长表格每帧 O(行数) 次解释器级比较）；
        改用切片等值比较（``data[:n] == cached`` 由 C 级 ``list.__eq__``
        完成）后同一判定一次完成，长表格每帧比较成本显著下降。
        """
        cached = self._data_src
        n = len(cached)
        # 纯追加快路径（含等长无变化）：data 以 cached 为前缀 → 无需改动
        if len(data) >= n and data[:n] == cached:
            return
        md = min(len(data), n)
        common = 0
        while common < md and data[common] == cached[common]:
            common += 1
        if common >= len(cached):
            return
        # 头部滑窗探测：data[:len(cached)-k] == cached[k:]（截断移除了前 k 行）
        limit = min(self._MAX_SLIDE, len(cached) - 1)
        for k in range(1, limit + 1):
            if (len(data) >= len(cached) - k
                    and data[:len(cached) - k] == cached[k:]):
                del self._data_src[:k]
                del self._data_widths[:k]
                del self._data[:k]
                return
        # 前缀分歧：保留公共前缀，丢弃其余（含对应渲染行）
        del self._data_src[common:]
        del self._data_widths[common:]
        del self._data[common:]


__all__ = ["render_table", "TablePreviewCache"]
