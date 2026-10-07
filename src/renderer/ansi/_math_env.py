"""_math_env — LaTeX 数学环境渲染（矩阵 / cases / 对齐 / 数组）。

以 mixin 形式挂在 ``_math_latex._LatexRenderer`` 上，负责 ``\\begin{...}``
系列环境的终端排版：

  - **矩阵族**：matrix / pmatrix / bmatrix / Bmatrix / vmatrix / Vmatrix
    （含 ``*`` 变体）——列居中、可选自动伸缩定界符；
  - **cases 族**：cases / dcases / rcases / drcases——左对齐 + 左/右花括号；
  - **数组族**：array / darray / subarray——按 ``{lcr|}`` 列格式对齐并绘制竖线，
    支持 ``\\hline`` / ``\\hdashline`` / ``\\cline`` / ``\\toprule`` 等水平线；
  - **对齐族**：aligned / align / split / eqnarray / alignedat——按 ``&`` 分列，
    奇数列右对齐、偶数列左对齐（等式对齐语义）；
  - **居中对齐族**：gather / gathered / multline——单列居中（multline 首行左、
    末行右）。

单元格内容按完整 LaTeX 递归渲染，多行单元格（分数/根式/嵌套矩阵）按行内行高
垂直居中，消除「单元格只取首行」的内容丢失。
"""

from __future__ import annotations

from .helpers import AnsiLine

from ._math_style import _M_SYM, _M_HLINE
from ._math_box import (
    _Box, _empty_box, _split_rows, _split_cols, _wrap_delims, pad_to_height,
)
from ._math_cmds import HLINE_CHARS

# ── 环境分类 ───────────────────────────────────────────────

#: 矩阵环境 → (左定界符, 右定界符)
MATRIX_ENVS: dict[str, tuple[str, str]] = {
    "matrix": ("", ""), "pmatrix": ("(", ")"), "bmatrix": ("[", "]"),
    "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖"),
    "smallmatrix": ("", ""),
    "matrix*": ("", ""), "pmatrix*": ("(", ")"), "bmatrix*": ("[", "]"),
    "Bmatrix*": ("{", "}"), "vmatrix*": ("|", "|"), "Vmatrix*": ("‖", "‖"),
}

#: cases 环境 → (左定界符, 右定界符)
CASES_ENVS: dict[str, tuple[str, str]] = {
    "cases": ("{", ""), "cases*": ("{", ""),
    "dcases": ("{", ""), "dcases*": ("{", ""),
    "rcases": ("", "}"), "rcases*": ("", "}"),
    "drcases": ("", "}"), "drcases*": ("", "}"),
}

#: 需要读取列格式 ``{lcr|}`` 的数组环境
COLSPEC_ENVS: frozenset = frozenset({"array", "darray", "subarray"})

#: 等式对齐环境（& 分列：奇数列右对齐、偶数列左对齐）
ALIGN_ENVS: frozenset = frozenset({
    "aligned", "align", "align*", "alignedat", "alignedat*",
    "split", "eqnarray", "eqnarray*", "flalign", "flalign*",
    "IEEEeqnarray", "IEEEeqnarray*",
})

#: 单列居中环境
CENTER_ENVS: frozenset = frozenset({
    "gather", "gather*", "gathered", "lgathered", "rgathered",
})

#: 首行左 / 末行右 的多行环境
MULTLINE_ENVS: frozenset = frozenset({"multline", "multline*", "multlined"})

#: 认识的全部环境（未列出者按内容渲染，保持兼容）
KNOWN_ENVS: frozenset = frozenset(
    set(MATRIX_ENVS) | set(CASES_ENVS) | set(COLSPEC_ENVS)
    | set(ALIGN_ENVS) | set(CENTER_ENVS) | set(MULTLINE_ENVS)
)


def parse_colspec(spec: str) -> tuple[list[str], list[bool]]:
    """``{l|c|r}`` 列格式 → ``(列对齐表, 竖线位置表)``。

    支持带参数的列类型（``p{3cm}`` / ``m{2em}`` / ``b{1cm}`` / ``X`` 视为左
    对齐内容列，``>{...}<{...}`` 与 ``@{...}`` 修饰符跳过其参数）；竖线表长度
    = 列数 + 1：``bars[0]`` 为最左侧，``bars[i]`` 为第 i 列左侧，``bars[n]``
    为最右侧。无有效列时返回默认三列（``lcr``）。
    """
    aligns: list[str] = []
    bars: list[bool] = []
    pending_bar = False
    i = 0
    n = len(spec or "")
    while i < n:
        ch = spec[i]
        if ch in "lcr":
            bars.append(pending_bar)
            aligns.append(ch)
            pending_bar = False
            i += 1
            continue
        if ch in "pmbX":
            # 带参数列类型（``p{3cm}`` 等）：消费 ``{...}``，按左对齐内容列计
            end = _skip_braced(spec, i + 1)
            bars.append(pending_bar)
            aligns.append("l")
            pending_bar = False
            i = end
            continue
        if ch in "><@!":
            # 列修饰符（``>{...}`` / ``@{...}`` 等）：跳过其参数
            i = _skip_braced(spec, i + 1)
            continue
        if ch in "|:":
            if ch == "|":
                pending_bar = True
            i += 1
            continue
        i += 1
    if not aligns:
        return ["l", "c", "r"], [False, False, False, False]
    bars.append(pending_bar)
    return aligns, bars


def _skip_braced(spec: str, pos: int) -> int:
    """跳过 ``{...}`` 参数（``pos`` 起；无花括号时返回 ``pos``）。"""
    i = pos
    n = len(spec)
    while i < n and spec[i] != "{":
        # 花括号前只允许空白；遇到其它列标记则停止（不误吃后续列）
        if not spec[i].isspace():
            return pos
        i += 1
    if i >= n:
        return pos
    depth = 0
    while i < n:
        if spec[i] == "{":
            depth += 1
        elif spec[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def column_aligns(env: str, ncols: int) -> list[str]:
    """环境默认列对齐（无显式列格式时）。"""
    if env in CASES_ENVS:
        return ["l"] * ncols
    if env in ALIGN_ENVS:
        return ["r" if i % 2 == 0 else "l" for i in range(ncols)]
    if env == "eqnarray" or env == "eqnarray*":
        return ["r", "c", "l"][:ncols] or ["c"]
    return ["c"] * ncols


def _take_hline(text: str) -> tuple[str, str] | None:
    """行首是否为水平线命令 → ``(线型字符, 剩余文本)``，否则 ``None``。

    支持 ``\\hline`` / ``\\hdashline`` / ``\\toprule`` / ``\\midrule`` /
    ``\\bottomrule`` / ``\\cline{1-2}``（带参数），并允许命令后紧跟同行内容
    （如 ``\\hline c & d``）——剩余文本仍作为数据行解析。
    """
    s = text.strip()
    if not s.startswith("\\"):
        return None
    j = 1
    while j < len(s) and s[j].isalpha():
        j += 1
    name = s[1:j]
    if name not in HLINE_CHARS:
        return None
    k = j
    while k < len(s) and s[k] in " \t":
        k += 1
    if k < len(s) and s[k] in "{[":
        close = "}" if s[k] == "{" else "]"
        e = s.find(close, k)
        k = e + 1 if e > 0 else len(s)
    return HLINE_CHARS[name], s[k:]


class _MathEnvMixin:
    """环境渲染实现（由 ``_LatexRenderer`` 继承）。"""

    # ── 入口 ─────────────────────────────────────────────

    def _cmd_begin(self) -> _Box:
        env = self._read_group_raw().strip()
        colspec = ""
        if env in COLSPEC_ENVS:
            colspec = self._read_group_raw()
        elif env in ("alignedat", "alignedat*"):
            self._read_group_raw()   # 对齐点数量（& 分列已足够）
        elif env.endswith("*") and env[:-1] in MATRIX_ENVS:
            self._read_optional_raw()  # matrix* 的 [c] 列对齐（终端取默认）
        body = self._read_env_body(env)
        return self._render_environment(env, body, colspec)

    def _read_env_body(self, env: str) -> str:
        start = self.i
        depth = 1
        p = self.i
        while p < self.n:
            if self.s[p] == "\\":
                name = self._peek_cmd(p)
                q = p + 1 + len(name)
                if name == "begin":
                    depth += 1
                    p = q
                    continue
                if name == "end":
                    r = q
                    while r < self.n and self.s[r] == " ":
                        r += 1
                    if r < self.n and self.s[r] == "{":
                        e = self.s.find("}", r)
                        if e > 0:
                            r = e + 1
                    depth -= 1
                    if depth == 0:
                        body = self.s[start:p]
                        self.i = r
                        return body
                    p = r
                    continue
                p = q if q > p else p + 1
                continue
            p += 1
        body = self.s[start:self.n]
        self.i = self.n
        return body

    # ── 环境渲染 ─────────────────────────────────────────

    def _render_environment(self, env: str, body: str, colspec: str = "") -> _Box:
        try:
            return self._render_environment_impl(env, body, colspec)
        except Exception:
            return self._render_sub(body)

    def _render_environment_impl(self, env: str, body: str,
                                 colspec: str) -> _Box:
        if env not in KNOWN_ENVS:
            return self._render_sub(body)

        items = self._env_items(body)
        rows = [cells for kind, cells in items if kind == "row"]
        if not rows:
            return _empty_box()
        ncols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < ncols:
                r.append("")

        if env in COLSPEC_ENVS:
            aligns, bars = parse_colspec(colspec)
            while len(aligns) < ncols:
                aligns.append("l")
            bars = (bars + [False] * (ncols + 1))[:ncols + 1]
        else:
            aligns = column_aligns(env, ncols)
            bars = [False] * (ncols + 1)

        cell_boxes = [[self._env_cell(c) for c in row] for row in rows]
        row_aligns = None
        if env in MULTLINE_ENVS:
            row_aligns = ["l"] + ["c"] * max(0, len(cell_boxes) - 2) + ["r"]
        lines = self._layout_rows(cell_boxes, aligns, bars, items, row_aligns)

        if env in MULTLINE_ENVS:
            return _Box(lines)
        delims = MATRIX_ENVS.get(env) or CASES_ENVS.get(env)
        if delims and (delims[0] or delims[1]):
            return _wrap_delims(_Box(lines), delims[0], delims[1])
        return _Box(lines)

    def _env_items(self, body: str) -> list[tuple[str, list[str]]]:
        """环境体 → ``[("row", cells) | ("hline", [线型])]`` 序列。"""
        items: list[tuple[str, list[str]]] = []
        for raw in _split_rows(body):
            rest = raw
            while rest:
                taken = _take_hline(rest)
                if taken is None:
                    break
                items.append(("hline", [taken[0]]))
                rest = taken[1]
            if not rest.strip():
                continue
            cells = _split_cols(rest)
            items.append(("row", cells))
        return items

    def _env_cell(self, src: str) -> _Box:
        """单元格源码 → 布局块（空单元格为空块）。

        由 ``_LatexRenderer`` 实现（``_math_env`` 不反向导入解析主体，
        避免模块依赖环）。
        """
        raise NotImplementedError

    # ── 网格布局 ─────────────────────────────────────────

    def _layout_rows(self, cell_boxes: list[list[_Box]], aligns: list[str],
                     bars: list[bool],
                     items: list[tuple[str, list[str]]],
                     row_aligns: list[str] | None = None) -> list[AnsiLine]:
        """把单元格矩阵排成终端行（含竖线与水平线）。

        - 每行高度取该行单元格最大高度，单元格垂直居中；
        - 列宽取该列所有单元格最大宽度；
        - ``bars`` 控制列间竖线（``│``）与水平线交叉符（``┼``）；
        - ``row_aligns`` 非空时按行覆盖对齐（multline：首行左、末行右）。
        """
        ncols = len(aligns)
        widths = [0] * ncols
        for row in cell_boxes:
            for i, box in enumerate(row[:ncols]):
                if box.width > widths[i]:
                    widths[i] = box.width

        out: list[AnsiLine] = []
        left_bar = bool(bars[0]) if bars else False
        right_bar = bool(bars[ncols]) if len(bars) > ncols else False
        row_index = 0
        for idx, (kind, payload) in enumerate(items):
            if kind == "hline":
                out.append(self._hline_line(widths, bars, payload[0]))
                continue
            row = cell_boxes[row_index]
            row_align = (row_aligns[row_index]
                         if row_aligns and row_index < len(row_aligns) else None)
            row_index += 1
            height = max((b.height for b in row[:ncols]), default=1)
            padded = [pad_to_height(b, height) for b in row[:ncols]]
            while len(padded) < ncols:
                padded.append(_empty_box())
            for r in range(height):
                line = AnsiLine()
                if left_bar:
                    line.append("│ ", _M_SYM)
                for i in range(ncols):
                    if i:
                        line.append(" │ " if bars[i] else "  ",
                                    _M_SYM if bars[i] else None)
                    content = padded[i].lines[r] if r < padded[i].height else AnsiLine()
                    gap = widths[i] - content.width
                    align = row_align or aligns[i]
                    if align == "l":
                        lead = 0
                    elif align == "r":
                        lead = max(0, gap)
                    else:
                        lead = max(0, gap // 2)
                    trail = max(0, gap - lead)
                    if lead:
                        line.append(" " * lead)
                    for run in content.runs:
                        line.append_run(run)
                    if trail:
                        line.append(" " * trail)
                if right_bar:
                    line.append(" │", _M_SYM)
                out.append(line)
        return out

    @staticmethod
    def _hline_line(widths: list[int], bars: list[bool], ch: str) -> AnsiLine:
        """水平线行（列宽处铺线，竖线交叉处用 ``┼``，首尾竖线处用线端符）。"""
        ncols = len(widths)
        line = AnsiLine()
        if bars and bars[0]:
            line.append("─", _M_HLINE)
        for i in range(ncols):
            line.append(ch * max(0, widths[i]), _M_HLINE)
            if i < ncols - 1:
                if bars[i + 1]:
                    line.append(ch + "┼" + ch, _M_HLINE)
                else:
                    line.append(ch * 2, _M_HLINE)
        if len(bars) > ncols and bars[ncols]:
            line.append("─", _M_HLINE)
        return line


__all__ = [
    "MATRIX_ENVS", "CASES_ENVS", "COLSPEC_ENVS", "ALIGN_ENVS",
    "CENTER_ENVS", "MULTLINE_ENVS", "KNOWN_ENVS", "parse_colspec",
    "column_aligns", "_MathEnvMixin",
]
