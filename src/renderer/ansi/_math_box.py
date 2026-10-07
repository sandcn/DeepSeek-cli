"""_math_box — 数学终端排版的布局原语（``_Box`` + hjoin/vstack/定界符）。

从 ``_math_latex`` 拆出的纯布局层：不含解析、不含命令语义，只负责「多行
文本块」的组合与对齐。解析主体（``_math_latex``）、扩展命令
（``_math_cmds``）与环境渲染（``_math_env``）共用本层，避免三处各写一套
拼接逻辑（宽度补齐/基线对齐等细节只需维护一处）。
"""

from __future__ import annotations

from .helpers import AnsiLine
from .style import Style
from ._math_style import _M_SYM, _M_NUM, _M_OP, _M_FRAC, _M_ACCENT

from src.renderer.math_symbols.misc import _OPERATOR_CHARS


class _Box:
    """多行文本块（``AnsiLine`` 列表）。

    ``kind`` 标记大算符/极限函数/上下花括号等语义；``baseline`` 为基线行索引
    （水平拼接时按基线对齐，符合数学排版直觉：分数与主体行对齐、上下划线锚定
    内容行）。
    """

    __slots__ = ("lines", "kind", "baseline")

    def __init__(self, lines=None, kind: str | None = None,
                 baseline: int | None = None) -> None:
        self.lines = lines if lines else [AnsiLine()]
        self.kind = kind
        h = len(self.lines)
        if baseline is None:
            baseline = (h - 1) // 2
        self.baseline = max(0, min(baseline, h - 1))

    @property
    def width(self) -> int:
        return max((ln.width for ln in self.lines), default=0)

    @property
    def height(self) -> int:
        return len(self.lines)

    @property
    def plain(self) -> str:
        return "\n".join(ln.plain for ln in self.lines)


def _empty_box() -> _Box:
    return _Box([AnsiLine()])


def _txt(text: str, style=None, kind: str | None = None) -> _Box:
    if not text:
        return _Box([AnsiLine()], kind)
    return _Box([AnsiLine.of(text, style)], kind)


def _styled_text(text: str) -> _Box:
    """普通数学文本（数字/运算符/符号分色，相邻同类合并）。"""
    line = AnsiLine()
    for ch in text:
        if ch.isdigit():
            st = _M_NUM
        elif ch in _OPERATOR_CHARS:
            st = _M_OP
        else:
            st = _M_SYM
        line.append(ch, st)
    return _Box([line])


def _plain_of(box: _Box) -> str:
    return box.plain.replace("\n", " ")


def _has_operator(text: str) -> bool:
    """顶层是否含二元运算符（加括号判定用）。"""
    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "+-−=<>&|":
            return True
    return False


def _blank_line(width: int) -> AnsiLine:
    return AnsiLine.of(" " * width) if width > 0 else AnsiLine()


def _hjoin(boxes: list[_Box]) -> _Box:
    """水平拼接（按基线对齐，数学排版语义）。

    ★ 修复（多行元素列错位）：每个 box 的每一行按其**自身列宽**补齐——修复前
    空行/短行不补宽，后续 box 直接从当前已写列继续，导致多行元素（分数、根式、
    矩阵）与单行元素拼接时其非基线行（分子/分母）左移错位，如
    ``E = mc^2 + \\frac{a}{b}`` 的 ``a`` 出现在公式左端而非分数线中央上方。
    仅非末位 box 补齐（行尾不留无意义空格）。
    """
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return _empty_box()
    if len(boxes) == 1:
        return boxes[0]
    max_above = max(b.baseline for b in boxes)
    max_below = max(b.height - 1 - b.baseline for b in boxes)
    total = max_above + max_below + 1
    widths = [b.width for b in boxes]
    padded: list[list[AnsiLine]] = []
    for b in boxes:
        pad_top = max_above - b.baseline
        pad_bottom = total - pad_top - b.height
        padded.append(
            [AnsiLine() for _ in range(pad_top)] + b.lines
            + [AnsiLine() for _ in range(pad_bottom)]
        )
    out: list[AnsiLine] = []
    last = len(padded) - 1
    for r in range(total):
        line = AnsiLine()
        for bi, pl in enumerate(padded):
            ln = pl[r]
            for run in ln.runs:
                line.append_run(run)
            if bi < last:
                pad = widths[bi] - ln.width
                if pad > 0:
                    line.append(" " * pad)
        out.append(line)
    return _Box(out, baseline=max_above)


def _vstack(boxes: list[_Box], align: str = "center",
            baseline: int | None = None) -> _Box:
    """垂直堆叠（按最大宽度对齐：left/center/right）。"""
    if not boxes:
        return _empty_box()
    w = max(b.width for b in boxes)
    out: list[AnsiLine] = []
    for b in boxes:
        gap = w - b.width
        if align == "right":
            left = gap
        elif align == "left":
            left = 0
        else:
            left = gap // 2
        right = gap - left
        for ln in b.lines:
            nl = AnsiLine()
            if left:
                nl.append(" " * left)
            for run in ln.runs:
                nl.append_run(run)
            if right:
                nl.append(" " * right)
            out.append(nl)
    return _Box(out, baseline=baseline)


def pad_to_height(box: _Box, height: int, align: str = "center") -> _Box:
    """把 ``box`` 补到指定行高（内容按 ``align`` 垂直对齐）。

    矩阵/对齐环境的单元格含多行内容（分数、根式、嵌套矩阵）时按行高补齐，
    行间内容垂直居中（与 LaTeX 矩阵单元格语义一致）。
    """
    if height <= box.height:
        return box
    extra = height - box.height
    if align == "top":
        top = 0
    elif align == "bottom":
        top = extra
    else:
        top = extra // 2
    bottom = extra - top
    lines = [AnsiLine() for _ in range(top)] + list(box.lines) \
        + [AnsiLine() for _ in range(bottom)]
    return _Box(lines, kind=box.kind, baseline=box.baseline + top)


def repeat_line(text: str, count: int, style=None) -> AnsiLine:
    """重复字符构成一行（水平线/花括号/根号上划线共用）。"""
    return AnsiLine.of(text * max(0, count), style)


# ── 环境体拆分（顶层 \\ 行 / & 列） ─────────────────────────


def _split_rows(content: str) -> list[str]:
    rows: list[str] = []
    cur: list[str] = []
    depth = 0
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == "\\" and i + 1 < n and content[i + 1] == "\\":
            rows.append("".join(cur).strip())
            cur = []
            i += 2
            if i < n and content[i] == "[":
                e = content.find("]", i)
                if e > 0:
                    i = e + 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        rows.append("".join(cur).strip())
    return rows if rows else [content.strip()]


def _split_cols(row: str) -> list[str]:
    cols: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in row:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif ch == "&" and depth == 0:
            cols.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    cols.append("".join(cur).strip())
    return cols


# ── 多行定界符（自动伸缩括号） ─────────────────────────────


_TALL_LEFT = {"(": ("⎛", "⎜", "⎝"), "[": ("⎡", "⎢", "⎣"), "{": ("⎧", "⎪", "⎩"),
              "⟨": ("⎛", "⎜", "⎝"), "⌊": ("⎢", "⎢", "⎣"), "⌈": ("⎡", "⎢", "⎢")}
_TALL_RIGHT = {")": ("⎞", "⎟", "⎠"), "]": ("⎤", "⎥", "⎦"), "}": ("⎫", "⎪", "⎭"),
               "⟩": ("⎞", "⎟", "⎠"), "⌋": ("⎢", "⎢", "⎦"), "⌉": ("⎤", "⎥", "⎥")}

#: 花括号的「中段拐点」字符（``⎨`` / ``⎬``）——三段以上时中点行使用，
#: 使 ``cases`` 等多行内容呈现 ``⎧⎨⎩`` 完整分段（终端排版更接近印刷）。
_TALL_MID = {"{": "⎨", "}": "⎬"}


def _tall_delim(ch: str, idx: int, height: int) -> str:
    if not ch:
        return ""
    table = _TALL_LEFT if ch in _TALL_LEFT else _TALL_RIGHT
    pieces = table.get(ch)
    if not pieces or height <= 1:
        return ch
    if height == 2:
        return pieces[0] if idx == 0 else pieces[2]
    if idx == 0:
        return pieces[0]
    if idx == height - 1:
        return pieces[2]
    if idx == height // 2:
        mid = _TALL_MID.get(ch)
        if mid:
            return mid
    return pieces[1]


def _wrap_delims(content: _Box, left: str, right: str) -> _Box:
    """为多行内容加左右定界符（单行用普通括号，多行用高括号分段）。"""
    h = content.height
    lines: list[AnsiLine] = []
    for idx, ln in enumerate(content.lines):
        nl = AnsiLine()
        nl.append(_tall_delim(left, idx, h), _M_SYM)
        for run in ln.runs:
            nl.append_run(run)
        nl.append(_tall_delim(right, idx, h), _M_SYM)
        lines.append(nl)
    return _Box(lines, baseline=content.baseline)


def frac_box(num: _Box, den: _Box, bar_style=None) -> _Box:
    """分数二维排版（分子 / 分数线 / 分母，按中心对齐）。"""
    w = max(num.width, den.width) + 2
    bar = repeat_line("─", w, bar_style if bar_style is not None else _M_FRAC)
    return _vstack([num, _Box([bar]), den], align="center", baseline=num.height)


def accent_over(content: _Box, mark: str, style=None) -> _Box:
    """在内容上方叠加标记行（上划线/帽号/箭头等）。"""
    w = content.width
    bar = repeat_line(mark, w, style if style is not None else _M_ACCENT)
    return _vstack([_Box([bar]), content], align="left",
                   baseline=1 + content.baseline)


def accent_under(content: _Box, mark: str, style=None) -> _Box:
    """在内容下方叠加标记行（下划线等）。"""
    w = content.width
    bar = repeat_line(mark, w, style if style is not None else _M_ACCENT)
    return _vstack([content, _Box([bar])], align="left",
                   baseline=content.baseline)


#: 组合长删除线（U+0336）——``\cancel`` 逐字符叠加，不改变字符数（宽度稳定）
_COMBINING_STRIKE = "\u0336"


def strike_through(content: _Box, style=None) -> _Box:
    """给内容叠加删除线（``\\cancel`` / ``\\sout`` / ``\\bcancel``）。

    逐字符追加组合长删除线；空白字符保留不加线（避免行尾出现孤立斜线）。
    样式在原样式上合并 ``style``（渲染层传入「已取消」配色）；无 ``style``
    时保留原样式。
    """
    lines: list[AnsiLine] = []
    for ln in content.lines:
        nl = AnsiLine()
        for run in ln.runs:
            base = run.style if run.style is not None else Style()
            merged = base.merge(style) if style is not None else base
            text = "".join(
                " " if ch.isspace() else ch + _COMBINING_STRIKE
                for ch in run.text
            )
            nl.append(text, merged)
        lines.append(nl)
    return _Box(lines, kind=content.kind, baseline=content.baseline)


def bracket_over(content: _Box, mark: str = "\u23b4", style=None) -> _Box:
    """方括号上标注（``\\overbracket``；``\\overparen`` 传 ``⏜``）。"""
    return accent_over(content, mark, style)


def bracket_under(content: _Box, mark: str = "\u23b5", style=None) -> _Box:
    """方括号下标注（``\\underbracket``；``\\underparen`` 传 ``⏝``）。"""
    return accent_under(content, mark, style)


def join_right(line: AnsiLine, text: str, style=None) -> AnsiLine:
    """在行尾追加文本（复制行对象，避免就地修改缓存行）。"""
    nl = AnsiLine()
    for run in line.runs:
        nl.append_run(run)
    nl.append(text, style)
    return nl


def prepend(line: AnsiLine, text: str, style=None) -> AnsiLine:
    """在行首插入文本（复制行对象，避免就地修改缓存行）。"""
    nl = AnsiLine.of(text, style) if text else AnsiLine()
    for run in line.runs:
        nl.append_run(run)
    return nl


__all__ = [
    "_Box", "_empty_box", "_txt", "_styled_text", "_plain_of", "_has_operator",
    "_hjoin", "_vstack", "pad_to_height", "repeat_line", "_split_rows",
    "_split_cols", "_tall_delim", "_wrap_delims", "frac_box", "accent_over",
    "accent_under", "join_right", "prepend", "_blank_line",
    "strike_through", "bracket_over", "bracket_under",
]
