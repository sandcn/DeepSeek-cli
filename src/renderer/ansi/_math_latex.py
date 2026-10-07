"""_math_latex — LaTeX 数学公式 → AnsiLine 终端排版（零 Rich 依赖）。

TUI 内容路径（``src.renderer.ansi``）需要真正的数学公式终端渲染：原
``ansi/math.py`` 仅以等宽纯文本展示 LaTeX 源码（首版退化）。本模块提供
与 Rich 路径（``renderer.math_parser.MathParser``）**同源符号表**的渲染器，
输出 ``AnsiLine``（不引入 Rich）：

  - 符号表复用 ``renderer.math_symbols`` 的纯数据子模块（greek / relations /
    operators / arrows / functions / misc / delimiters / scripts）；
  - **块级**：真二维排版（分数堆叠、根式上划线、矩阵/cases 多行对齐）；
  - **行内**：紧凑单行（分数 ``a⁄b``、上下标 Unicode 化）。

设计：递归下降解析 + ``_Box`` 行布局原语（hjoin/vstack）。所有命令与 Rich
路径保持同一覆盖集合；未知命令原样保留（不吞内容）。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine, Run

from src.renderer.math_symbols.greek import _GREEK_LETTERS
from src.renderer.math_symbols.relations import _RELATION_SYMBOLS
from src.renderer.math_symbols.operators import (
    _OPERATOR_SYMBOLS, _BIG_OPERATORS, _BIG_OPERATOR_COMMANDS,
)
from src.renderer.math_symbols.arrows import _ARROW_SYMBOLS, _LOGICAL_ARROWS
from src.renderer.math_symbols.functions import _FUNCTION_NAMES, _LIMIT_FUNCTIONS
from src.renderer.math_symbols.misc import (
    _MISC_SYMBOLS, _ACCENT_MAP, _OPERATOR_CHARS, _SILENT_COMMANDS,
)
from src.renderer.math_symbols.delimiters import _DELIMITER_MAP, _SPACE_MAP
from src.renderer.math_symbols.scripts import _SUPERSCRIPT_MAP, _SUBSCRIPT_MAP


# ═══════════════════════════════════════════════════════════
# 样式常量（ansi Style —— 无 strike，取消态用暗红）
# ═══════════════════════════════════════════════════════════

_M_SYM = Style(fg=252)
_M_NUM = Style(fg=221)
_M_OP = Style(fg=51, bold=True)
_M_FN = Style(fg=75)
_M_BIGOP = Style(fg=51, bold=True)
_M_SUP = Style(fg=51)
_M_SUB = Style(fg=245)
_M_FRAC = Style(fg=213)
_M_TEXT = Style(fg=245, italic=True)
_M_TEXT_BOLD = Style(fg=245, italic=True, bold=True)
_M_SQRT = Style(fg=213)
_M_ACCENT = Style(fg=51, dim=True)
_M_BOX = Style(fg=226)
_M_CANCEL = Style(fg=203, dim=True)
_M_TAG = Style(fg=240, dim=True)
_M_NOTICE = Style(fg=240, italic=True, dim=True)
_M_FENCE = Style(fg=45, bold=True)

#: 颜色名 → 256 色号（``\\color{name}`` 着色；与 ansi inline 走同一取值风格）
_COLOR_256: dict[str, int] = {
    "red": 196, "green": 40, "blue": 33, "yellow": 220,
    "cyan": 44, "magenta": 201, "white": 231, "black": 0,
    "gray": 240, "grey": 240, "darkred": 88, "darkgreen": 22,
    "darkblue": 18, "orange": 214, "purple": 93, "pink": 213,
    "teal": 44, "brown": 130, "navy": 25, "lime": 118,
    "olive": 142, "violet": 177, "gold": 220, "silver": 250,
}

#: 全命令映射（字符 + 样式类别）——一次构建，程序期不变。
_CMD_MAP: dict[str, tuple[str, Style]] = {}


def _build_cmd_map() -> dict[str, tuple[str, Style]]:
    m: dict[str, tuple[str, Style]] = {}
    for table in (_GREEK_LETTERS, _RELATION_SYMBOLS, _ARROW_SYMBOLS, _MISC_SYMBOLS):
        for cmd, char in table.items():
            m[cmd] = (char, _M_SYM)
    for cmd, char in _OPERATOR_SYMBOLS.items():
        m[cmd] = (char, _M_OP)
    for cmd, char in _BIG_OPERATORS.items():
        m[cmd] = (char, _M_BIGOP)
    for cmd, name in _FUNCTION_NAMES.items():
        m[cmd] = (name, _M_FN)
    for cmd, space in _SPACE_MAP.items():
        m.setdefault(cmd, (space, _M_SYM))
    return m


_CMD_MAP = _build_cmd_map()

#: 需要加括号（避免歧义）的顶层运算符字符
_OPEN_PAREN_OPS = frozenset("+-−=<>&|")

#: 解析器特殊字符（触发按命令/分组/脚本处理）
_SPECIAL_CHARS = frozenset("\\{}^_&~$")

#: 二维矩阵环境 → 定界符对（None = 无定界符）
_MATRIX_DELIMS: dict[str, tuple[str, str, bool]] = {
    "matrix": ("", "", False),
    "pmatrix": ("(", ")", False),
    "bmatrix": ("[", "]", False),
    "Bmatrix": ("{", "}", False),
    "vmatrix": ("|", "|", False),
    "Vmatrix": ("‖", "‖", False),
    "cases": ("{", "", True),
    "array": ("", "", False),
    "aligned": ("", "", False),
    "align": ("", "", False),
    "align*": ("", "", False),
    "gathered": ("", "", False),
    "split": ("", "", False),
    "smallmatrix": ("", "", False),
    "subarray": ("", "", False),
}

#: 解析/布局最大递归深度（防异常输入）
_MAX_DEPTH = 24


# ═══════════════════════════════════════════════════════════
# _Box — 行布局原语
# ═══════════════════════════════════════════════════════════


class _Box:
    """多行文本块（``AnsiLine`` 列表）。

    ``kind`` 标记大算符/极限函数；``baseline`` 为基线行索引（水平拼接时按
    基线对齐，符合数学排版直觉：分数与主体行对齐、上下划线锚定内容行）。
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


def _txt(text: str, style: Style | None = None, kind: str | None = None) -> _Box:
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
        elif depth == 0 and ch in _OPEN_PAREN_OPS:
            return True
    return False


# ═══════════════════════════════════════════════════════════
# 环境体拆分（顶层 \\ 行 / & 列）
# ═══════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════
# LaTeX 渲染器
# ═══════════════════════════════════════════════════════════


class _LatexRenderer:
    """LaTeX 子集 → ``_Box``（块级二维 / 行内紧凑）。"""

    def __init__(self, src: str, inline: bool = False) -> None:
        self.s = src or ""
        self.n = len(self.s)
        self.i = 0
        self.inline = inline
        self.depth = 0

    # ── 入口 ──────────────────────────────────────────

    def render(self) -> _Box:
        if self.depth > _MAX_DEPTH:
            return _txt(self.s[self.i:])
        self.depth += 1
        try:
            return self._parse_seq()
        finally:
            self.depth -= 1

    # ── 序列 / 原子 ───────────────────────────────────

    def _parse_seq(self, stop: frozenset = frozenset({"}"})) -> _Box:
        parts: list[_Box] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c in stop:
                break
            if c == "{":
                parts.append(self._parse_group())
                continue
            if c == "}":
                self.i += 1
                continue
            if c == "\\":
                if self._peek_is_end():
                    break
                parts.append(self._parse_command())
                continue
            if c in "^_":
                base = parts.pop() if parts else None
                parts.append(self._parse_scripts(base))
                continue
            if c == "&":
                break
            if c.isspace():
                while self.i < self.n and self.s[self.i].isspace():
                    self.i += 1
                if parts:
                    parts.append(_txt(" "))
                continue
            if c == "~":
                self.i += 1
                parts.append(_txt(" "))
                continue
            parts.append(self._parse_text_run())
        return _hjoin(parts)

    def _parse_text_run(self) -> _Box:
        start = self.i
        while (self.i < self.n
               and self.s[self.i] not in _SPECIAL_CHARS
               and not self.s[self.i].isspace()):
            self.i += 1
        if self.i == start:
            self.i += 1
            text = self.s[start]
        else:
            text = self.s[start:self.i]
        return _styled_text(text)

    def _parse_atom(self) -> _Box:
        if self.i >= self.n:
            return _empty_box()
        c = self.s[self.i]
        if c == "{":
            return self._parse_group()
        if c == "\\":
            return self._parse_command()
        if c == "~":
            self.i += 1
            return _txt(" ")
        if c.isspace():
            while self.i < self.n and self.s[self.i].isspace():
                self.i += 1
            return _txt(" ")
        if c in "^_":
            return self._parse_scripts(None)
        return self._parse_text_run()

    def _parse_group(self) -> _Box:
        self.i += 1  # 跳过 '{'
        box = self._parse_seq(frozenset({"}"}))
        if self.i < self.n and self.s[self.i] == "}":
            self.i += 1
        return box

    def _read_group_raw(self) -> str:
        """读取 ``{...}`` 原始内容（消费花括号；无花括号时取单个原子原文）。"""
        while self.i < self.n and self.s[self.i] == " ":
            self.i += 1
        if self.i >= self.n:
            return ""
        if self.s[self.i] == "{":
            depth = 1
            start = self.i + 1
            j = start
            while j < self.n and depth > 0:
                if self.s[j] == "{":
                    depth += 1
                elif self.s[j] == "}":
                    depth -= 1
                j += 1
            raw = self.s[start:j - 1] if depth == 0 else self.s[start:j]
            self.i = j
            return raw
        if self.s[self.i] == "\\":
            start = self.i
            self.i += 1
            while self.i < self.n and self.s[self.i].isalpha():
                self.i += 1
            return self.s[start:self.i]
        ch = self.s[self.i]
        self.i += 1
        return ch

    def _render_sub(self, text: str) -> _Box:
        return _LatexRenderer(text, inline=self.inline).render()

    # ── 脚本（^ / _） ─────────────────────────────────

    def _parse_scripts(self, base: _Box | None) -> _Box:
        sup: _Box | None = None
        sub: _Box | None = None
        while self.i < self.n and self.s[self.i] in "^_":
            is_sup = self.s[self.i] == "^"
            self.i += 1
            arg = self._parse_atom()
            if is_sup:
                sup = arg
            else:
                sub = arg
        return self._attach(_Box([AnsiLine()], base.kind if base else None)
                            if base is None else base, sup, sub)

    def _attach(self, base: _Box, sup: _Box | None, sub: _Box | None) -> _Box:
        kind = base.kind
        if kind == "limit":
            return self._attach_limit(base, sup, sub)
        if kind == "bigop":
            return self._attach_bigop(base, sup, sub)
        if base.height != 1:
            return self._attach_flat(base, sup, sub)
        line = AnsiLine()
        for run in base.lines[0].runs:
            line.append_run(run)
        if sup is not None:
            self._append_script(line, sup, is_sup=True)
        if sub is not None:
            self._append_script(line, sub, is_sup=False)
        return _Box([line])

    def _append_script(self, line: AnsiLine, box: _Box, is_sup: bool) -> None:
        plain = _plain_of(box).strip()
        if not plain:
            return
        mapping = _SUPERSCRIPT_MAP if is_sup else _SUBSCRIPT_MAP
        style = _M_SUP if is_sup else _M_SUB
        if all(ch in mapping or ch.isspace() for ch in plain):
            line.append("".join(mapping.get(ch, ch) for ch in plain), style)
            return
        delim = "^" if is_sup else "_"
        line.append(delim + "{" + plain + "}", style)

    def _attach_flat(self, base: _Box, sup: _Box | None, sub: _Box | None) -> _Box:
        """多行 base（分数/根式/矩阵）的脚本：附加为紧凑文本（不破坏二维布局）。"""
        lines = list(base.lines)
        first = AnsiLine()
        for run in lines[0].runs:
            first.append_run(run)
        if sup is not None:
            self._append_script(first, sup, is_sup=True)
        lines[0] = first
        if sub is not None:
            last = AnsiLine()
            for run in lines[-1].runs:
                last.append_run(run)
            self._append_script(last, sub, is_sup=False)
            lines[-1] = last
        return _Box(lines)

    def _attach_bigop(self, base: _Box, sup: _Box | None, sub: _Box | None) -> _Box:
        line = AnsiLine()
        for run in base.lines[0].runs:
            line.append_run(run)
        if sub is not None:
            line.append("_{" + _plain_of(sub).strip() + "}", _M_SUB)
        if sup is not None:
            line.append("^{" + _plain_of(sup).strip() + "}", _M_SUP)
        return _Box([line])

    def _attach_limit(self, base: _Box, sup: _Box | None, sub: _Box | None) -> _Box:
        line = AnsiLine()
        for run in base.lines[0].runs:
            line.append_run(run)
        inner = ""
        if sub is not None:
            inner = _plain_of(sub).strip()
        if sup is not None:
            s = _plain_of(sup).strip()
            inner = f"{inner} → {s}" if inner else s
        if inner:
            line.append("(" + inner + ")", _M_NOTICE)
        return _Box([line])

    # ── 命令 ──────────────────────────────────────────

    def _peek_is_end(self) -> bool:
        return (self.s.startswith("\\end", self.i)
                and not self._is_alpha_at(self.i + 4))

    def _is_alpha_at(self, pos: int) -> bool:
        return 0 <= pos < self.n and self.s[pos].isalpha()

    def _peek_cmd(self, pos: int) -> str:
        if pos >= self.n or self.s[pos] != "\\":
            return ""
        j = pos + 1
        while j < self.n and self.s[j].isalpha():
            j += 1
        return self.s[pos + 1:j]

    def _parse_command(self) -> _Box:
        self.i += 1  # 跳过 '\\'
        if self.i >= self.n:
            return _txt("\\")
        c = self.s[self.i]
        if not c.isalpha():
            self.i += 1
            if c in _SPACE_MAP:
                return _txt(_SPACE_MAP[c])
            return _txt(c)

        start = self.i
        while self.i < self.n and self.s[self.i].isalpha():
            self.i += 1
        cmd = self.s[start:self.i]
        # 命令后星的 ``\operatorname*``
        starred = False
        if cmd == "operatorname" and self.i < self.n and self.s[self.i] == "*":
            starred = True
            self.i += 1

        return self._dispatch_command(cmd, starred)

    def _dispatch_command(self, cmd: str, starred: bool) -> _Box:
        if cmd in ("frac", "tfrac", "dfrac", "cfrac"):
            return self._cmd_frac()
        if cmd == "sqrt":
            return self._cmd_sqrt()
        if cmd in ("text", "textbf", "textit", "mathrm", "mathbf", "mathcal",
                   "mathit", "mathbb", "mathscr", "boldsymbol", "bm",
                   "textnormal", "normalfont", "mathbfit", "mathsf", "mathtt",
                   "mathfrak", "Bbb", "cal", "rm", "it", "sc", "sf", "tt"):
            return self._cmd_text(cmd)
        if cmd in ("left", "right", "middle"):
            return self._cmd_delimiter()
        if cmd in ("big", "Big", "bigg", "Bigg", "bigl", "Bigl", "biggl",
                   "Biggl", "bigr", "Bigr", "biggr", "Biggr", "bigm", "Bigm",
                   "biggm", "Biggm"):
            return self._cmd_delimiter()
        if cmd in ("lvert", "rvert", "lVert", "rVert", "langle", "rangle",
                   "lfloor", "rfloor", "lceil", "rceil"):
            return _txt(_DELIMITER_MAP.get(cmd, cmd), _M_SYM)
        if cmd == "begin":
            return self._cmd_begin()
        if cmd == "end":
            return _empty_box()
        if cmd in _ACCENT_MAP:
            return self._cmd_accent(cmd)
        if cmd == "binom":
            return self._cmd_binom()
        if cmd in ("underset", "overset", "stackrel"):
            return self._cmd_stacked(cmd)
        if cmd in ("cancel", "bcancel", "xcancel", "sout", "cancelto"):
            return self._cmd_cancel(cmd)
        if cmd in ("color", "textcolor"):
            return self._cmd_color(cmd)
        if cmd == "boxed":
            return self._cmd_boxed()
        if cmd == "operatorname":
            return self._cmd_operatorname(starred)
        if cmd in ("overline", "underline"):
            return self._cmd_overline(cmd)
        if cmd == "substack":
            return self._cmd_substack()
        if cmd in ("xrightarrow", "xleftarrow", "xmapsto", "xRightarrow",
                   "xLeftarrow", "xLeftrightarrow", "xleftrightarrow",
                   "xhookrightarrow", "xhookleftarrow", "xlongequal",
                   "xrightleftharpoons", "xleftrightharpoons",
                   "xrightharpoonup", "xrightharpoondown",
                   "xleftharpoonup", "xleftharpoondown"):
            return self._cmd_xarrow()
        if cmd in ("overrightarrow", "overleftarrow", "overleftrightarrow",
                   "underrightarrow", "underleftarrow"):
            return self._cmd_vector(cmd)
        if cmd in ("abs", "norm"):
            return self._cmd_abs(cmd)
        if cmd in ("mod", "pmod", "pod", "bmod"):
            return self._cmd_mod(cmd)
        if cmd == "tag":
            return self._cmd_tag()
        if cmd in ("displaystyle", "textstyle", "scriptstyle",
                   "scriptscriptstyle", "limits", "nolimits"):
            return _empty_box()
        if cmd in ("not",):
            return _txt("¬", _M_OP)
        if cmd in ("mathbin", "mathrel", "mathord", "mathop", "mathinner"):
            return self._render_sub(self._read_group_raw())
        if cmd in _SILENT_COMMANDS:
            if cmd in ("hspace", "vspace", "hphantom", "vphantom", "phantom",
                       "raisebox", "kern", "mkern", "mskip", "hrule"):
                self._read_group_raw()
            if cmd in ("intertext", "shortintertext"):
                raw = self._read_group_raw()
                return _txt(raw, _M_TEXT)
            if cmd == "label" or cmd == "ref" or cmd == "eqref":
                self._read_group_raw()
            return _empty_box()

        # ── 逻辑箭头（自动加空格）───────────────────────
        if cmd in _LOGICAL_ARROWS:
            symbol = _LOGICAL_ARROWS[cmd].strip()
            return _txt(f"  {symbol}  ", _M_OP)

        # 符号表 / 大算符 / 函数 / 间距
        entry = _CMD_MAP.get(cmd)
        if entry is not None:
            char, style = entry
            if cmd in _LIMIT_FUNCTIONS:
                return _txt(char, _M_FN, kind="limit")
            if cmd in _BIG_OPERATOR_COMMANDS:
                return _txt(char, _M_BIGOP, kind="bigop")
            if style is _M_FN:
                char = char + " " if self._next_starts_atom() else char
            return _txt(char, style)

        return _txt("\\" + cmd, _M_SYM)

    def _next_starts_atom(self) -> bool:
        if self.i >= self.n:
            return False
        c = self.s[self.i]
        return c.isalnum() or c == "\\" or c == "{"

    # ── 具体命令实现 ──────────────────────────────────

    def _cmd_frac(self) -> _Box:
        num = self._render_sub(self._read_group_raw())
        den = self._render_sub(self._read_group_raw())
        if self.inline:
            ns = _plain_of(num)
            ds = _plain_of(den)
            if _has_operator(ns):
                ns = "(" + ns + ")"
            if _has_operator(ds):
                ds = "(" + ds + ")"
            return _txt(ns + "⁄" + ds, _M_SYM)
        w = max(num.width, den.width) + 2
        bar = AnsiLine.of("─" * w, _M_FRAC)
        return _vstack([num, _Box([bar]), den], align="center", baseline=num.height)

    def _cmd_sqrt(self) -> _Box:
        # 可选次数 \sqrt[3]{x}
        degree = ""
        if self.i < self.n and self.s[self.i] == "[":
            e = self.s.find("]", self.i)
            if e > 0:
                degree = self.s[self.i + 1:e].strip()
                self.i = e + 1
        content = self._render_sub(self._read_group_raw())
        if self.inline:
            line = AnsiLine.of((degree if degree else "") + "√", _M_SQRT)
            for run in content.lines[0].runs:
                line.append_run(run)
            return _Box([line])
        w = content.width
        top = AnsiLine.of(" " + "‾" * w, _M_SQRT)
        lines = [top]
        for idx, ln in enumerate(content.lines):
            nl = AnsiLine.of("√" if idx == 0 else " ", _M_SQRT)
            for run in ln.runs:
                nl.append_run(run)
            lines.append(nl)
        return _Box(lines, baseline=1 + content.baseline)

    def _cmd_text(self, cmd: str) -> _Box:
        raw = self._read_group_raw()
        bold = cmd in ("textbf", "mathbf", "boldsymbol", "bm", "mathbfit")
        return _txt(raw, _M_TEXT_BOLD if bold else _M_TEXT)

    def _cmd_delimiter(self) -> _Box:
        while self.i < self.n and self.s[self.i] == " ":
            self.i += 1
        if self.i >= self.n:
            return _empty_box()
        c = self.s[self.i]
        if c == "\\":
            name = self._peek_cmd(self.i)
            self.i += 1 + len(name)
            return _txt(_DELIMITER_MAP.get(name, name), _M_SYM)
        self.i += 1
        return _txt(_DELIMITER_MAP.get(c, c), _M_SYM)

    def _cmd_accent(self, cmd: str) -> _Box:
        content = self._render_sub(self._read_group_raw())
        mark = _ACCENT_MAP.get(cmd, "")
        if content.height != 1 or not mark:
            return content
        line = AnsiLine()
        for run in content.lines[0].runs:
            line.append_run(run)
        line.append(mark, _M_ACCENT)
        return _Box([line])

    def _cmd_binom(self) -> _Box:
        top = _plain_of(self._render_sub(self._read_group_raw()))
        bot = _plain_of(self._render_sub(self._read_group_raw()))
        return _txt(f"({top}¦{bot})", _M_SYM)

    def _cmd_stacked(self, cmd: str) -> _Box:
        # 上下标注在终端近似为「仅显示主体」（标注不参与布局）
        self._read_group_raw()
        second = self._read_group_raw()
        return self._render_sub(second)

    def _cmd_cancel(self, cmd: str) -> _Box:
        content = self._render_sub(self._read_group_raw())
        if cmd == "cancelto":
            self._read_group_raw()  # 目标值
        lines: list[AnsiLine] = []
        for ln in content.lines:
            nl = AnsiLine()
            for run in ln.runs:
                st = run.style
                nl.append_run(Run(run.text, _M_CANCEL if st is None else st))
            lines.append(nl)
        return _Box(lines)

    def _cmd_color(self, cmd: str) -> _Box:
        name = self._read_group_raw().strip()
        content = self._render_sub(self._read_group_raw())
        style = _color_style(name)
        lines: list[AnsiLine] = []
        for ln in content.lines:
            nl = AnsiLine()
            for run in ln.runs:
                nl.append_run(Run(run.text, style))
            lines.append(nl)
        return _Box(lines)

    def _cmd_boxed(self) -> _Box:
        content = self._render_sub(self._read_group_raw())
        if self.inline:
            return _txt("[" + _plain_of(content) + "]", _M_BOX)
        w = content.width
        top = AnsiLine.of("┌" + "─" * (w + 2) + "┐", _M_BOX)
        bottom = AnsiLine.of("└" + "─" * (w + 2) + "┘", _M_BOX)
        lines = [top]
        for ln in content.lines:
            nl = AnsiLine.of("│ ", _M_BOX)
            for run in ln.runs:
                nl.append_run(run)
            pad = w - ln.width
            if pad > 0:
                nl.append(" " * pad)
            nl.append(" │", _M_BOX)
            lines.append(nl)
        lines.append(bottom)
        return _Box(lines, baseline=1 + content.baseline)

    def _cmd_operatorname(self, starred: bool) -> _Box:
        raw = self._read_group_raw()
        return _txt(raw, _M_FN, kind="limit" if starred else None)

    def _cmd_overline(self, cmd: str) -> _Box:
        content = self._render_sub(self._read_group_raw())
        w = content.width
        bar = AnsiLine.of(("‾" if cmd == "overline" else "_") * w, _M_ACCENT)
        if cmd == "overline":
            return _vstack([_Box([bar]), content], align="left",
                           baseline=1 + content.baseline)
        return _vstack([content, _Box([bar])], align="left",
                       baseline=content.baseline)

    def _cmd_substack(self) -> _Box:
        raw = self._read_group_raw()
        parts = [_LatexRenderer(seg, inline=self.inline).render()
                 for seg in _split_rows(raw)]
        return _vstack(parts, align="center")

    def _cmd_xarrow(self) -> _Box:
        label = self._read_group_raw()
        text = _plain_of(self._render_sub(label))
        return _txt(f"──{text}──▶" if text else "──▶", _M_OP)

    def _cmd_vector(self, cmd: str) -> _Box:
        content = self._render_sub(self._read_group_raw())
        if cmd.startswith("under"):
            return content
        if content.height != 1:
            return content
        line = AnsiLine()
        for run in content.lines[0].runs:
            line.append_run(run)
        line.append("\u20D7", _M_ACCENT)
        return _Box([line])

    def _cmd_abs(self, cmd: str) -> _Box:
        content = _plain_of(self._render_sub(self._read_group_raw()))
        if cmd == "norm":
            return _txt(f"‖{content}‖", _M_SYM)
        return _txt(f"|{content}|", _M_SYM)

    def _cmd_mod(self, cmd: str) -> _Box:
        if cmd == "bmod":
            return _txt(" mod ", _M_FN)
        raw = self._read_group_raw()
        inner = _plain_of(_LatexRenderer(raw, inline=True).render())
        if cmd == "pmod":
            return _txt(f" (mod {inner})", _M_FN)
        if cmd == "pod":
            return _txt(f" ({inner})", _M_FN)
        return _txt(inner, _M_FN)

    def _cmd_tag(self) -> _Box:
        raw = self._read_group_raw()
        return _txt("  (" + raw + ")", _M_TAG)

    # ── 环境（矩阵 / cases） ──────────────────────────

    def _cmd_begin(self) -> _Box:
        env = self._read_group_raw().strip()
        if env == "array":
            self._read_group_raw()  # 列格式 {lcr}
        body = self._read_env_body(env)
        if env in _MATRIX_DELIMS:
            return self._render_matrix(env, body)
        # 未知环境：按内容渲染
        return self._render_sub(body)

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

    def _render_matrix(self, env: str, body: str) -> _Box:
        left, right, brace = _MATRIX_DELIMS[env]
        rows = _split_rows(body)
        cells = [[_LatexRenderer(c, inline=self.inline).render() for c in _split_cols(r)]
                 for r in rows]
        ncols = max((len(r) for r in cells), default=1)
        # 列宽
        widths = [0] * ncols
        for row in cells:
            for idx, cell in enumerate(row):
                if cell.width > widths[idx]:
                    widths[idx] = cell.width
        align = "left" if brace else "center"
        out: list[AnsiLine] = []
        for row in cells:
            line = AnsiLine()
            for idx in range(ncols):
                cell = row[idx] if idx < len(row) else None
                if idx:
                    line.append("  ")
                if cell is None:
                    continue
                pad = widths[idx] - cell.width
                lead = 0 if align == "left" else pad // 2
                trail = pad - lead
                if lead:
                    line.append(" " * lead)
                for run in cell.lines[0].runs:
                    line.append_run(run)
                if trail:
                    line.append(" " * trail)
            out.append(line)
        inner = _Box(out)
        if not left and not right:
            return inner
        return _wrap_delims(inner, left, right)


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


_TALL_LEFT = {"(": ("⎛", "⎜", "⎝"), "[": ("⎡", "⎢", "⎣"), "{": ("⎧", "⎪", "⎩"),
              "|": ("⎢", "⎢", "⎢"), "‖": ("⎢", "⎢", "⎢")}
_TALL_RIGHT = {")": ("⎞", "⎟", "⎠"), "]": ("⎤", "⎥", "⎦"), "}": ("⎫", "⎪", "⎭"),
               "|": ("⎢", "⎢", "⎢"), "‖": ("⎢", "⎢", "⎢")}


def _tall_delim(ch: str, idx: int, height: int) -> str:
    if not ch:
        return ""
    table = _TALL_LEFT if ch in _TALL_LEFT else _TALL_RIGHT
    pieces = table.get(ch)
    if not pieces or height <= 1:
        return ch
    if idx == 0:
        return pieces[0]
    if idx == height - 1:
        return pieces[2]
    return pieces[1]


def _color_style(name: str) -> Style:
    if name.startswith("#") and len(name) == 7:
        try:
            r = int(name[1:3], 16)
            g = int(name[3:5], 16)
            b = int(name[5:7], 16)
            from .style import rgb_to_256
            return Style(fg=rgb_to_256(r, g, b))
        except ValueError:
            return _M_SYM
    return Style(fg=_COLOR_256.get(name.lower(), 231))


# ═══════════════════════════════════════════════════════════
# 公共入口（供 ansi/math.py 使用）
# ═══════════════════════════════════════════════════════════


def render_math_box(source: str, inline: bool = False) -> _Box:
    """LaTeX 源码 → 布局 ``_Box``（行内紧凑 / 块级二维）。"""
    try:
        return _LatexRenderer(source or "", inline=inline).render()
    except Exception:
        return _txt(source or "", _M_SYM)


__all__ = ["_Box", "render_math_box"]
