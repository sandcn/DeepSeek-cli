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
COLSPEC_ENVS: frozenset = frozenset({
    "array", "darray", "subarray",
    # ★ 第二批扩展：``tabular`` 族（含 ``&`` 分列，此前落到未知名环境分支
    #   会因 ``&`` 截断内容）
    "tabular", "longtable", "supertabular", "tabu",
})

#: 需先跳过「宽度参数」再读列格式的环境（``tabular*{宽}{列格式}``）
WIDTH_COLSPEC_ENVS: frozenset = frozenset({
    "tabular*", "tabularx", "tabulary",
})

#: 等式对齐环境（& 分列：奇数列右对齐、偶数列左对齐）
ALIGN_ENVS: frozenset = frozenset({
    "aligned", "align", "align*", "alignedat", "alignedat*",
    "alignat", "alignat*",
    "split", "eqnarray", "eqnarray*", "flalign", "flalign*",
    "IEEEeqnarray", "IEEEeqnarray*",
})

#: 单列居中环境
CENTER_ENVS: frozenset = frozenset({
    "gather", "gather*", "gathered", "lgathered", "rgathered",
    # ★ 扩展：单列公式环境（``$$`` 内嵌套 ``\begin{equation}`` 时按居中块渲染，
    #   修复前落到「未知名环境」分支——内容保留但结构/对齐丢失）
    "equation", "equation*", "displaymath", "math", "dmath",
    "dgroup", "mathdisplay",
})

#: 首行左 / 末行右 的多行环境
MULTLINE_ENVS: frozenset = frozenset({"multline", "multline*", "multlined"})

#: 交换图环境（``\begin{CD} A @>a>> B \\ @VbVV @AAcA \\ C @= D \end{CD}``）
CD_ENVS: frozenset = frozenset({"CD", "cd"})

#: 认识的全部环境（未列出者按内容渲染，保持兼容）
KNOWN_ENVS: frozenset = frozenset(
    set(MATRIX_ENVS) | set(CASES_ENVS) | set(COLSPEC_ENVS)
    | set(WIDTH_COLSPEC_ENVS)
    | set(ALIGN_ENVS) | set(CENTER_ENVS) | set(MULTLINE_ENVS)
    | set(CD_ENVS)
)

def _cd_arrow_text(kind: str, label: str) -> str:
    """交换图箭头记号 → 终端可读文本。"""
    if kind == ">":
        return "──" + label + "─▶"
    if kind == "<":
        return "◀─" + label + "──"
    if kind == "V":
        return "│" + label + "│↓"
    if kind == "A":
        return "↑│" + label + "│"
    if kind == "=":
        return "═" + label + "═"
    if kind == "|":
        return "│"
    return ""


def _cd_line_to_cells(line: str) -> list[str]:
    """交换图一行 → 单元格序列（``@`` 箭头记号转箭头文本，其余按空白分格）。

    箭头语法（amscd）：``@>label>>`` / ``@<label<<`` / ``@VlabelVV`` /
    ``@AlabelA`` / ``@=label=`` / ``@|`` / ``@.``——标签读到终止字符为止。
    """
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(line)

    def _flush() -> None:
        text = "".join(buf).strip()
        if text:
            cells.extend(text.split())
        buf.clear()

    while i < n:
        ch = line[i]
        if ch == "@" and i + 1 < n:
            kind = line[i + 1]
            i += 2
            if kind in "<>VA=":
                label: list[str] = []
                if kind == "=":
                    while i < n and line[i] != "=":
                        label.append(line[i])
                        i += 1
                    i += 1
                else:
                    endc = kind
                    while i < n and line[i] != endc:
                        label.append(line[i])
                        i += 1
                    if i < n:
                        i += 1
                    if kind != "A":
                        while i < n and line[i] == endc:
                            i += 1
                _flush()
                cells.append(_cd_arrow_text(kind, "".join(label).strip()))
                continue
            if kind == "|":
                _flush()
                cells.append(_cd_arrow_text("|", ""))
                continue
            if kind == ".":
                continue
            buf.append("@")
            buf.append(kind)
            continue
        buf.append(ch)
        i += 1
    _flush()
    return cells


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
            else:
                # ``:`` 为虚线分隔（KaTeX/array 语义）——终端以实线竖线近似
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
        custom = getattr(self.macro_state, "custom_envs", {}).get(env)
        if custom is not None:
            return self._render_custom_env(env, custom)
        # 环境位置参数（``\begin{array}[t]{cc}`` / ``\begin{matrix*}[r]``）：
        # 终端无垂直定位概念，统一忽略，避免其被当作列格式内容。
        self._read_optional_raw()
        colspec = ""
        if env in COLSPEC_ENVS:
            colspec = self._read_group_raw()
        elif env in WIDTH_COLSPEC_ENVS:
            self._read_group_raw()      # 宽度参数（终端按内容宽度自适应）
            colspec = self._read_group_raw()
        elif env in ("alignedat", "alignedat*"):
            self._read_group_raw()   # 对齐点数量（& 分列已足够）
        body = self._read_env_body(env)
        return self._render_environment(env, body, colspec)

    def _render_custom_env(self, env: str,
                           spec: tuple[int, str, str]) -> _Box:
        """``\\newenvironment`` 登记的自定义环境：begin/end 代码包裹正文。

        参数（``[n]``）以 ``#1``… 形式替换进 begin/end 代码；正文按完整
        LaTeX 递归渲染（内容不丢）。
        """
        nargs, begin_body, end_body = spec
        args = [self._read_group_raw() for _ in range(max(0, nargs))]
        body = self._read_env_body(env)
        src = begin_body + body + end_body
        for idx, value in enumerate(args, 1):
            src = src.replace("#" + str(idx), value)
        return self._render_sub(src)

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
            # 渲染异常兜底：``&`` 会被序列解析当作列分隔而截断内容——替换为
            # 空格后重渲染，保证异常环境下内容不丢。
            return self._render_sub(body.replace("&", " "))

    def _render_environment_impl(self, env: str, body: str,
                                 colspec: str) -> _Box:
        if env not in KNOWN_ENVS:
            # 未登记环境：内容原样呈现（``&`` 转空格，避免序列解析截断内容）
            return self._render_sub(body.replace("&", " "))

        if env in CD_ENVS:
            return self._render_cd(body)

        items = self._env_items(body)
        rows = [cells for kind, cells in items if kind == "row"]
        if not rows:
            return _empty_box()
        ncols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < ncols:
                r.append("")

        if env in COLSPEC_ENVS or env in WIDTH_COLSPEC_ENVS:
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

    def _render_cd(self, body: str) -> _Box:
        """交换图环境（``CD``）：``@`` 箭头记号转文本 + 按行/列网格对齐。"""
        rows = [r for r in (_cd_line_to_cells(line) for line in _split_rows(body))
                if r]
        if not rows:
            return _empty_box()
        ncols = max(len(r) for r in rows)
        for r in rows:
            while len(r) < ncols:
                r.append("")
        aligns = ["c"] * ncols
        bars = [False] * (ncols + 1)
        cell_boxes = [[self._env_cell(c) for c in row] for row in rows]
        lines = self._layout_rows(cell_boxes, aligns, bars,
                                  [("row", r) for r in rows], None)
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
