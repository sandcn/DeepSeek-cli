"""_math_tex — TeX 原语与旧式排版结构（零 Rich，供 ANSI 公式路径使用）。

KaTeX 兼容层，补齐 ``\\over`` / ``\\atop`` / ``\\choose`` / ``\\above`` /
``\\brace`` / ``\\brack`` 及其 ``*withdelims`` 变体（组内分数原语），以及
``\\matrix`` / ``\\pmatrix`` / ``\\cases`` / ``\\array`` / ``\\displaylines``
/ ``\\eqalign`` 等旧式结构，与位置微调命令（``\\raise`` / ``\\lower`` /
``\\moveleft`` / ``\\moveright`` / ``\\skew`` / ``\\vcenter`` …）。

解析主体（``_math_latex``）负责在 ``{...}`` 组与顶层检测原语并调用
``_render_tex_primitive``；本模块只实现原语的语义（如何组织 ``_Box``）。

扩展方式：新原语登记到 ``_FRAC_PRIMITIVES`` 或 ``COMMAND_HANDLERS``。
"""

from __future__ import annotations

from .helpers import AnsiLine

from ._math_style import _M_SYM, _M_NOTICE
from ._math_box import (
    _Box, _txt, _empty_box, _plain_of, _vstack, _wrap_delims, frac_box,
)
from ._math_cmds import COMMAND_HANDLERS
from src.renderer.math_symbols.delimiters import _DELIMITER_MAP

#: 组内/顶层分数类原语（在此集合中的命令触发「左右两半」语义）
_FRAC_PRIMITIVES: frozenset = frozenset({
    "over", "atop", "choose", "brace", "brack", "above",
    "overwithdelims", "atopwithdelims", "abovewithdelims",
})

#: 二项式类原语 → (左定界符, 右定界符)；``choose`` 带圆括号
_BINOM_PRIMITIVES: dict[str, tuple[str, str]] = {
    "choose": ("(", ")"),
    "brace": ("{", "}"),
    "brack": ("[", "]"),
}

#: 旧式结构命令 → 等价环境名（``\\pmatrix{a&b}`` ≡ ``\\begin{pmatrix}``）
_LEGACY_ENVS: dict[str, str] = {
    "matrix": "matrix", "pmatrix": "pmatrix", "bmatrix": "bmatrix",
    "Bmatrix": "Bmatrix", "vmatrix": "vmatrix", "Vmatrix": "Vmatrix",
    "smallmatrix": "smallmatrix", "cases": "cases",
    "array": "array", "darray": "darray",
    "displaylines": "gather", "eqalign": "aligned",
}

#: 只消耗位置参数、内容照常渲染的命令（终端无精确位置概念）
_POSITION_ARG_CMDS: frozenset = frozenset({
    "raise", "lower", "moveleft", "moveright", "shoveleft", "shoveright",
    "uproot", "leftroot",
})


def _read_delim_at(s: str, i: int) -> tuple[str, int]:
    """从 ``s[i:]`` 读取一个定界符 → ``(显示字符, 新位置)``。"""
    n = len(s)
    while i < n and s[i] == " ":
        i += 1
    if i >= n:
        return "", i
    ch = s[i]
    if ch == "\\":
        if i + 1 < n and not s[i + 1].isalpha():
            nxt = s[i + 1]
            return _DELIMITER_MAP.get("\\" + nxt, nxt), i + 2
        j = i + 1
        while j < n and s[j].isalpha():
            j += 1
        name = s[i + 1:j]
        if name:
            return _DELIMITER_MAP.get(name, name), j
        return "", i + 1
    return _DELIMITER_MAP.get(ch, ch), i + 1


def _read_dim_at(s: str, i: int) -> tuple[str, int]:
    """从 ``s[i:]`` 读取一个尺寸参数（``{1pt}`` / ``2pt``）→ ``(原文, 新位置)``。"""
    n = len(s)
    while i < n and s[i] == " ":
        i += 1
    if i >= n:
        return "", i
    if s[i] == "{":
        depth = 0
        j = i
        while j < n:
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], n
    j = i
    while j < n and not s[j].isspace() and s[j] not in "{}":
        j += 1
    return s[i:j], j


def _split_at_primitive(raw: str):
    """扫描顶层（花括号外）的分数类原语。

    Returns:
        ``None``（无原语）或 ``(name, before, after, delim_left,
        delim_right, dim)``——``before`` / ``after`` 为原语前后原文；定界符与
        尺寸仅对相应变体有意义（无则空串）。
    """
    depth = 0
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if ch == "\\" and depth == 0:
            j = i + 1
            while j < n and raw[j].isalpha():
                j += 1
            name = raw[i + 1:j]
            if name in _FRAC_PRIMITIVES:
                left = right = dim = ""
                k = j
                if name in ("overwithdelims", "atopwithdelims",
                            "abovewithdelims"):
                    left, k = _read_delim_at(raw, k)
                    right, k = _read_delim_at(raw, k)
                if name in ("above", "abovewithdelims"):
                    dim, k = _read_dim_at(raw, k)
                return name, raw[:i], raw[k:], left, right, dim
            i = j if j > i else i + 1
            continue
        i += 1
    return None


class _MathTexMixin:
    """TeX 原语与旧式结构（由 ``_LatexRenderer`` 继承）。"""

    # ── 分数类原语（组内 / 顶层） ────────────────────────

    def _render_group_raw(self, raw: str) -> _Box:
        """渲染花括号组原文：先检测顶层分数原语，否则递归解析。"""
        info = _split_at_primitive(raw)
        if info is None:
            return self._render_sub(raw)
        name, before, after, left, right, _dim = info
        return self._render_tex_primitive(name, before, after, left, right)

    def _render_tex_primitive(self, name: str, before: str, after: str,
                              left: str = "", right: str = "") -> _Box:
        """按 TeX 原语语义组织左右两半内容。"""
        num = self._render_sub(before) if before.strip() else _empty_box()
        den = self._render_sub(after) if after.strip() else _empty_box()
        if name == "over":
            return frac_box(num, den) if not self.inline else \
                self._inline_frac(num, den, "", "")
        if name == "atop":
            box = _vstack([num, den], align="center")
            return _wrap_delims(box, left, right) if (left or right) else box
        if name in _BINOM_PRIMITIVES:
            dl, dr = (left, right) if (left or right) else _BINOM_PRIMITIVES[name]
            if self.inline:
                top = _plain_of(num).strip()
                bot = _plain_of(den).strip()
                return _txt(f"{dl}{top}¦{bot}{dr}", _M_SYM)
            return _wrap_delims(_vstack([num, den], align="center"), dl, dr)
        if name in ("above", "abovewithdelims"):
            box = frac_box(num, den)
            return _wrap_delims(box, left, right) if (left or right) else box
        # overwithdelims / 未登记变体：分数 + 定界符
        box = frac_box(num, den) if not self.inline else \
            self._inline_frac(num, den, left, right)
        if self.inline:
            return box
        return _wrap_delims(box, left, right) if (left or right) else box

    def _inline_frac(self, num: _Box, den: _Box, left: str, right: str) -> _Box:
        ns = _plain_of(num).strip()
        ds = _plain_of(den).strip()
        return _txt(f"{left}{ns}⁄{ds}{right}", _M_SYM)

    # ── 旧式结构命令（\matrix / \cases / \array …） ──────

    def _cmd_legacy_env(self, cmd: str) -> _Box:
        """``\\matrix{a&b\\\\c&d}`` 等旧式结构（无 ``\\begin`` / ``\\end``）。"""
        env = _LEGACY_ENVS.get(cmd)
        if env is None:
            return self._render_sub(self._read_group_raw())
        body = self._read_group_raw()
        colspec = ""
        if cmd in ("array", "darray"):
            # plain TeX 的 ``\array`` 参数即列格式（``{lcr}``），内容紧随其后
            colspec = body
            body = self._read_group_raw()
        try:
            return self._render_environment(env, body, colspec)
        except Exception:
            return self._render_sub(body.replace("&", " "))

    def _cmd_eqalign(self, cmd: str) -> _Box:
        """``\\eqalign`` / ``\\eqalignno`` / ``\\leqalignno``（对齐 + 行号）。"""
        body = self._read_group_raw()
        numbered = cmd in ("eqalignno", "leqalignno")
        try:
            box = self._render_environment("aligned", body, "")
        except Exception:
            box = self._render_sub(body.replace("&", " "))
        if not numbered:
            return box
        # 行号列（plain TeX 的 ``\eqalignno`` 每行第三列是编号）
        rows = [r for r in _split_env_rows(body)]
        tags = []
        for row in rows:
            cols = _split_top_cols(row)
            tags.append(cols[2].strip() if len(cols) > 2 else "")
        if not any(tags):
            return box
        lines = list(box.lines)
        for idx, tag in enumerate(tags):
            if idx < len(lines) and tag:
                lines[idx] = _join_text(lines[idx], "  (" + tag + ")", _M_NOTICE)
        return _Box(lines, baseline=box.baseline)

    # ── 位置微调命令（参数忽略，内容照常渲染） ───────────

    def _cmd_position(self, cmd: str) -> _Box:
        """``\\raise{1em}{...}`` 等：终端无精确位置，忽略参数渲染内容。"""
        if cmd in ("uproot", "leftroot"):
            self._read_group_raw()
            return self._render_atom_for_position()
        if cmd in ("shoveleft", "shoveright"):
            return self._render_sub(self._read_group_raw())
        self._read_group_raw()
        return self._render_atom_for_position()

    def _render_atom_for_position(self) -> _Box:
        """渲染位置命令后的目标原子（组 / 命令 / 单字符）。"""
        if self.i >= self.n:
            return _empty_box()
        if self.s[self.i] == "{":
            return self._render_sub(self._read_group_raw())
        return self._parse_atom()

    # ── 其它原语 ────────────────────────────────────────

    def _cmd_env_sep(self, cmd: str) -> _Box:
        """环境分隔命令在环境外的降级（``\\cr`` 换行 / ``\\and`` 空格）。

        环境内由 ``_split_rows`` / ``_split_cols`` 提前处理，不会走到这里；
        环境外保证不残留命令文本。
        """
        if cmd == "cr":
            if self.inline:
                return _txt(" ")
            return _Box([AnsiLine()], kind="newline")
        return _txt(" ")

    def _cmd_multicolumn(self, cmd: str = "multicolumn") -> _Box:
        """``\\multicolumn{n}{fmt}{content}`` 在环境外渲染其内容。"""
        self._read_group_raw()
        self._read_group_raw()
        return self._render_sub(self._read_group_raw())

    def _cmd_skew(self, cmd: str) -> _Box:
        r"""``\skew{6}\hat{a}``：忽略偏移量，渲染其后重音命令。"""
        self._read_group_raw()
        if self.i < self.n and self.s[self.i] == "\\":
            return self._parse_command()
        return self._render_atom_for_position()

    def _cmd_buildrel(self, cmd: str) -> _Box:
        """``a \\buildrel f \\over \\longrightarrow b``：把上方内容叠到下方关系符上。"""
        # 读取上方内容直到顶层 ``\over``
        above_src = ""
        j = self.i
        depth = 0
        while j < self.n:
            ch = self.s[j]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth = max(0, depth - 1)
            elif ch == "\\" and depth == 0 and self.s.startswith("\\over", j) \
                    and not self._is_alpha_at(j + 5):
                break
            j += 1
        above_src = self.s[self.i:j]
        if j < self.n:
            self.i = j + 5
        else:
            self.i = j
        base = self._render_atom_for_position()
        label = _plain_of(self._render_sub(above_src)).strip()
        if not label:
            return base
        return _vstack([_txt(label, _M_NOTICE), base], align="center",
                       baseline=1 + base.baseline)

    def _cmd_rule_tex(self, cmd: str) -> _Box:
        """``\\vrule`` / ``\\Rule``：竖条 / 实心条（无参数时给默认尺寸）。"""
        if cmd == "Rule":
            return self._cmd_rule(cmd)
        h = 1
        if self.i < self.n and self.s[self.i] in "wh":
            spec, self.i = _read_dim_at(self.s, self.i + 1)
            from ._math_cmds import _length_to_chars
            h = max(1, min(4, _length_to_chars(spec, default=1)))
        return _Box([AnsiLine.of("│", _M_SYM) for _ in range(h)])

    def _cmd_vcenter(self, cmd: str) -> _Box:
        """``\\vcenter{...}``：内容垂直居中（终端即原样内容）。"""
        return self._render_sub(self._read_group_raw())

    def _cmd_char(self, cmd: str) -> _Box:
        """``\\char"263a`` / ``\\unicode{x263a}`` → 对应 Unicode 字符。"""
        if cmd == "unicode":
            raw = self._read_group_raw().strip()
            return _txt(_parse_codepoint(raw), _M_SYM)
        spec = ""
        if self.i < self.n and self.s[self.i] in "\"'":
            quote = self.s[self.i]
            self.i += 1
            j = self.i
            while j < self.n and (self.s[j].isalnum()):
                j += 1
            spec = self.s[self.i:j]
            self.i = j
            if quote == "'" and spec:
                spec = str(int(spec, 8)) if spec.isdigit() else ""
            elif quote == '"':
                spec = str(int(spec, 16)) if spec else ""
        else:
            spec, self.i = _read_dim_at(self.s, self.i)
        return _txt(_parse_codepoint(spec), _M_SYM)


def _parse_codepoint(spec: str) -> str:
    """码点说明（十进制 / ``0x...`` / ``"..."`` 十六进制）→ 字符。"""
    s = (spec or "").strip()
    if not s:
        return ""
    try:
        if s.lower().startswith("0x"):
            return chr(int(s[2:], 16))
        if s.startswith("x") or s.startswith("X"):
            return chr(int(s[1:], 16))
        if s.startswith('"'):
            return chr(int(s[1:], 16))
        if s.startswith("'"):
            return chr(int(s[1:], 8))
        if s.isdigit():
            return chr(int(s))
    except (ValueError, OverflowError):
        return ""
    return s


def _split_env_rows(body: str) -> list[str]:
    """环境体按顶层 ``\\\\`` / ``\\cr`` 拆行。"""
    rows: list[str] = []
    cur: list[str] = []
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            if ch == "\\" and i + 1 < n and body[i + 1] == "\\":
                rows.append("".join(cur).strip())
                cur = []
                i += 2
                continue
            if body.startswith("\\cr", i) and not _is_alpha_at_str(body, i + 3):
                rows.append("".join(cur).strip())
                cur = []
                i += 3
                continue
        cur.append(ch)
        i += 1
    if cur:
        rows.append("".join(cur).strip())
    return rows


def _is_alpha_at_str(s: str, pos: int) -> bool:
    return 0 <= pos < len(s) and s[pos].isalpha()


def _split_top_cols(row: str) -> list[str]:
    """按顶层 ``&`` / ``\\and`` 拆列。"""
    cols: list[str] = []
    cur: list[str] = []
    depth = 0
    i = 0
    n = len(row)
    while i < n:
        ch = row[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            if ch == "&":
                cols.append("".join(cur))
                cur = []
                i += 1
                continue
            if row.startswith("\\and", i) and not _is_alpha_at_str(row, i + 4):
                cols.append("".join(cur))
                cur = []
                i += 4
                continue
        cur.append(ch)
        i += 1
    cols.append("".join(cur))
    return cols


def _join_text(line: AnsiLine, text: str, style=None) -> AnsiLine:
    out = AnsiLine()
    for run in line.runs:
        out.append_run(run)
    out.append(text, style)
    return out


def _register_tex_commands() -> dict[str, str]:
    table: dict[str, str] = {}
    for cmd in _LEGACY_ENVS:
        table[cmd] = "_cmd_legacy_env"
    for cmd in ("eqalign", "eqalignno", "leqalignno"):
        table[cmd] = "_cmd_eqalign"
    for cmd in _POSITION_ARG_CMDS:
        table[cmd] = "_cmd_position"
    table["skew"] = "_cmd_skew"
    table["buildrel"] = "_cmd_buildrel"
    table["cr"] = "_cmd_env_sep"
    table["and"] = "_cmd_env_sep"
    table["multicolumn"] = "_cmd_multicolumn"
    table["vrule"] = "_cmd_rule_tex"
    table["Rule"] = "_cmd_rule_tex"
    table["vcenter"] = "_cmd_vcenter"
    table["char"] = "_cmd_char"
    table["unicode"] = "_cmd_char"
    return table


COMMAND_HANDLERS.update(_register_tex_commands())


__all__ = ["_MathTexMixin", "_FRAC_PRIMITIVES", "_split_at_primitive"]
