"""_math_latex — LaTeX 数学公式 → AnsiLine 终端排版（零 Rich 依赖）。

TUI 内容路径（``src.renderer.ansi``）的数学公式渲染器：与 Rich 路径
（``renderer.math_parser.MathParser``）**同源符号表**，输出 ``AnsiLine``
（不引入 Rich）。

  - 符号表复用 ``renderer.math_symbols`` 的纯数据子模块（greek / relations /
    operators / arrows / functions / misc / delimiters / scripts）；
  - **块级**：真二维排版（分数堆叠、根式上划线、矩阵/对齐环境多行对齐、
    大算符上下限堆叠、上下标注、上下花括号、自动伸缩定界符）；
  - **行内**：紧凑单行（分数 ``a⁄b``、上下标 Unicode 化）。

模块划分（本文件为解析主体 + 命令分派）：
  - ``_math_box``   — 布局原语（``_Box`` / hjoin / vstack / 定界符）
  - ``_math_style`` — 样式常量与颜色解析
  - ``_math_letters`` — Unicode 数学字母族（``\\mathbb`` / ``\\mathcal`` …）
  - ``_math_cmds``  — 扩展命令（overset / brace / colorbox / 字体族 …）
  - ``_math_env``   — 环境（矩阵 / cases / array / 对齐 / multline）

扩展方式：新增命令登记到 ``_math_cmds.COMMAND_HANDLERS``，无需改动本文件。
"""

from __future__ import annotations

from .helpers import AnsiLine, Run
from .style import Style

from ._math_style import (
    _M_SYM, _M_OP, _M_BIGOP, _M_SUP, _M_SUB, _M_NOTICE, _M_FN, _M_TEXT,
    _M_SQRT, _M_CANCEL, _M_TAG, _M_BOX, _M_ARROW, color_style,
)
from ._math_box import (
    _Box, _empty_box, _txt, _styled_text, _hjoin, _vstack, _plain_of,
    _has_operator, _split_rows, _wrap_delims, frac_box, strike_through,
)
from ._math_cmds import COMMAND_HANDLERS, _MathCommandMixin, _restyle_box
from ._math_cmds import _operatorname_text
from ._math_env import _MathEnvMixin
from ._math_tex import _MathTexMixin, _split_at_primitive
from ._math_macros import _MathMacroMixin, MacroState
from ._math_html import _MathHtmlMixin

from src.renderer.math_symbols.greek import _GREEK_LETTERS
from src.renderer.math_symbols.relations import _RELATION_SYMBOLS
from src.renderer.math_symbols.operators import (
    _OPERATOR_SYMBOLS, _BIG_OPERATORS, _BIG_OPERATOR_COMMANDS,
)
from src.renderer.math_symbols.arrows import _ARROW_SYMBOLS, _LOGICAL_ARROWS
from src.renderer.math_symbols.functions import _FUNCTION_NAMES, _LIMIT_FUNCTIONS
from src.renderer.math_symbols.misc import (
    _MISC_SYMBOLS, _ACCENT_MAP, _SILENT_COMMANDS,
)
from src.renderer.math_symbols.delimiters import _DELIMITER_MAP, _SPACE_MAP
from src.renderer.math_symbols.scripts import _SUPERSCRIPT_MAP, _SUBSCRIPT_MAP
from src.renderer.math_symbols.negations import negate_symbol

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

#: 文本模式重音字符（``\'{a}`` / ``\^{a}`` …）；对应 ``\`` 后的非字母字符
_TEXT_ACCENT_CHARS = frozenset("'`^\"~=.")

#: 解析/布局最大递归深度（防异常输入）
_MAX_DEPTH = 24


class _LatexRenderer(_MathCommandMixin, _MathEnvMixin, _MathTexMixin,
                     _MathMacroMixin, _MathHtmlMixin):
    """LaTeX 子集 → ``_Box``（块级二维 / 行内紧凑）。"""

    def __init__(self, src: str, inline: bool = False,
                 macro_state: MacroState | None = None,
                 preprocess: bool = True) -> None:
        self.s = src or ""
        self.n = len(self.s)
        self.i = 0
        self.inline = inline
        self.depth = 0
        #: 宏系统跨公式共享状态（``\gdef`` 持久；``None`` 时新建局部状态）
        self.macro_state = macro_state if macro_state is not None else MacroState()
        self.macro_table = None
        #: 是否在 ``render()`` 入口做宏预处理（子渲染器已在展开后的文本上工作）
        self._preprocess_src = preprocess
        #: ``\displaystyle`` 生效中——行内公式下大算符也按上下限堆叠
        self.displaystyle = False
        #: ``\limits`` / ``\nolimits`` 强制上下限位置（``None`` = 按模式默认）
        self.limits_mode: bool | None = None

    # ── 入口 ──────────────────────────────────────────

    def render(self) -> _Box:
        if self.depth > _MAX_DEPTH:
            return _txt(self.s[self.i:])
        self.depth += 1
        try:
            if self._preprocess_src:
                self.s = self.preprocess(self.s)
                self.n = len(self.s)
            if self.i == 0 and "\\" in self.s:
                if _split_at_primitive(self.s) is not None:
                    return self._render_group_raw(self.s)
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
                if (not self.inline and self.i + 1 < self.n
                        and self.s[self.i + 1] == "\\"):
                    # 块级公式中的硬换行 ``\\``（KaTeX 在 display math 允许）：
                    # 拆成多行垂直堆叠；行内公式忽略（与 KaTeX 一致）。
                    self.i += 2
                    if self.i < self.n and self.s[self.i] == "[":
                        end = self.s.find("]", self.i)
                        if end > 0:
                            self.i = end + 1
                    parts.append(_Box([AnsiLine()], kind="newline"))
                    continue
                parts.append(self._parse_command())
                continue
            if c in "^_":
                base = parts.pop() if parts else None
                # LaTeX 语义：命令与脚本之间的空白不影响绑定（``\sum _{i}``
                # 等价 ``\sum_{i}``）——弹出末尾的纯空白块，避免脚本错误地
                # 绑定到空白而非前一个原子。
                if base is not None and not _plain_of(base).strip():
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
        return self._join_seq_parts(parts)

    @staticmethod
    def _join_seq_parts(parts: list[_Box]) -> _Box:
        """拼接序列块；含块级硬换行标记时按行垂直堆叠。"""
        if any(p.kind == "newline" for p in parts):
            rows: list[_Box] = []
            cur: list[_Box] = []
            for p in parts:
                if p.kind == "newline":
                    rows.append(_hjoin(cur))
                    cur = []
                else:
                    cur.append(p)
            rows.append(_hjoin(cur))
            return _vstack(rows, align="center")
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

    def _parse_script_atom(self) -> _Box:
        """脚本（``^`` / ``_``）参数原子：``{...}`` 组 / 命令 / 单个字符。

        TeX 语义：``x^2,`` 的 ``^`` 只取紧随的**单个** token（``2``），``,``
        属于普通文本；``x^ab`` 同理只取 ``a``。修复前按普通文本段读取
        （``2,`` / ``ab`` 整体成为上标），导致 ``cases`` 等场景出现
        ``x^{2,}`` 这类错误排版。
        """
        if self.i >= self.n:
            return _empty_box()
        c = self.s[self.i]
        if c == "{":
            return self._parse_group()
        if c == "\\":
            return self._parse_command()
        if c.isspace():
            while self.i < self.n and self.s[self.i].isspace():
                self.i += 1
            return _txt(" ")
        self.i += 1
        return _styled_text(c)

    def _parse_group(self) -> _Box:
        """``{...}`` 组：读取原文后交给 ``_render_group_raw``。

        组内可能含 TeX 分数原语（``{a \\over b}``）——必须先检测再递归解析。
        """
        return self._render_group_raw(self._read_group_raw())

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
        return _LatexRenderer(text, inline=self.inline,
                              macro_state=self.macro_state,
                              preprocess=False).render()

    # ── 脚本（^ / _） ─────────────────────────────────

    def _parse_scripts(self, base: _Box | None) -> _Box:
        sup: _Box | None = None
        sub: _Box | None = None
        while self.i < self.n and self.s[self.i] in "^_":
            is_sup = self.s[self.i] == "^"
            self.i += 1
            arg = self._parse_script_atom()
            if is_sup:
                sup = arg
            else:
                sub = arg
        return self._attach(_Box([AnsiLine()], base.kind if base else None)
                            if base is None else base, sup, sub)

    def _attach(self, base: _Box, sup: _Box | None, sub: _Box | None) -> _Box:
        kind = base.kind
        if kind in ("overbrace", "underbrace"):
            return self._attach_brace(base, sup, sub)
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
        r"""大算符脚本：块级上下限堆叠（∑ 上下），行内紧凑 ``_{}^{}``。

        上下限按完整布局块堆叠（多行内容如分数上下限整体呈现），并以对应
        脚本配色重着色（上标亮青、下标灰）。``\displaystyle`` 生效时行内
        公式同样堆叠（与 LaTeX 语义一致）；``\limits`` / ``\nolimits``
        强制上下限位置。
        """
        stack_limits = self._should_stack_limits()
        if stack_limits and base.height == 1:
            has_sup = sup is not None and _plain_of(sup).strip() != ""
            has_sub = sub is not None and _plain_of(sub).strip() != ""
            if has_sup or has_sub:
                parts: list[_Box] = []
                if has_sup:
                    parts.append(_restyle_box(sup, _M_SUP))
                parts.append(base)
                if has_sub:
                    parts.append(_restyle_box(sub, _M_SUB))
                return _vstack(parts, align="center",
                               baseline=1 if has_sup else 0)
        line = AnsiLine()
        for run in base.lines[0].runs:
            line.append_run(run)
        if sub is not None:
            line.append("_{" + _plain_of(sub).strip() + "}", _M_SUB)
        if sup is not None:
            line.append("^{" + _plain_of(sup).strip() + "}", _M_SUP)
        return _Box([line])

    def _should_stack_limits(self) -> bool:
        """大算符/极限函数的上下限是否堆叠（``\\limits`` / ``\\nolimits`` 优先）。"""
        if self.limits_mode is not None:
            return self.limits_mode
        return not self.inline or self.displaystyle

    def _attach_limit(self, base: _Box, sup: _Box | None, sub: _Box | None) -> _Box:
        """极限函数脚本：块级下标置于下方，行内紧凑 ``(x → 0)``。"""
        inner = ""
        if sub is not None:
            inner = _plain_of(sub).strip()
        if sup is not None:
            s = _plain_of(sup).strip()
            inner = f"{inner} → {s}" if inner else s
        if self._should_stack_limits() and base.height == 1 \
                and sub is not None and _plain_of(sub).strip() != "":
            label = _plain_of(sub).strip()
            if sup is not None and _plain_of(sup).strip():
                label = f"{label} → {_plain_of(sup).strip()}"
            return _vstack([base, _txt(label, _M_NOTICE)], align="center",
                           baseline=0)
        line = AnsiLine()
        for run in base.lines[0].runs:
            line.append_run(run)
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
            if c in _TEXT_ACCENT_CHARS:
                return self._cmd_text_accent_char(c)
            if c == "\\":
                # 硬换行（``\\``）：环境内由 ``_split_rows`` 处理，块外显示换行符
                return _txt("⏎", _M_NOTICE)
            return _txt(c)

        start = self.i
        while self.i < self.n and self.s[self.i].isalpha():
            self.i += 1
        cmd = self.s[start:self.i]
        starred = False
        if cmd == "operatorname" and self.i < self.n and self.s[self.i] == "*":
            starred = True
            self.i += 1
        return self._dispatch_command(cmd, starred)

    def _dispatch_command(self, cmd: str, starred: bool) -> _Box:
        # ── 扩展命令注册表（_math_cmds）──
        handler = COMMAND_HANDLERS.get(cmd)
        if handler is not None:
            method = getattr(self, handler, None)
            if method is not None:
                return method(cmd)

        if cmd in ("frac", "tfrac", "dfrac", "cfrac"):
            return self._cmd_frac()
        if cmd == "sqrt":
            return self._cmd_sqrt()
        if cmd == "begin":
            return self._cmd_begin()
        if cmd == "end":
            return _empty_box()
        if cmd in _ACCENT_MAP:
            return self._cmd_accent(cmd)
        if cmd == "binom":
            return self._cmd_binom()
        if cmd in ("left", "right", "middle"):
            return self._cmd_left_series(cmd)
        if cmd in ("big", "Big", "bigg", "Bigg", "bigl", "Bigl", "biggl",
                   "Biggl", "bigr", "Bigr", "biggr", "Biggr", "bigm", "Bigm",
                   "biggm", "Biggm"):
            return _txt(self._read_delimiter_char(), _M_SYM)
        if cmd in ("lvert", "rvert", "lVert", "rVert", "langle", "rangle",
                   "lfloor", "rfloor", "lceil", "rceil"):
            return _txt(_DELIMITER_MAP.get(cmd, cmd), _M_SYM)
        # KaTeX 定界符别名（\vert / \Vert / \lparen / \lbrack / \lBrace /
        # \llbracket / \lang / \arrowvert …）——不在符号表时按定界符字符渲染
        if cmd in _DELIMITER_MAP and cmd not in _CMD_MAP:
            return _txt(_DELIMITER_MAP[cmd], _M_SYM)
        if cmd == "cancelto":
            return self._cmd_cancel(cmd)
        if cmd in ("bcancel", "xcancel", "sout", "cancel"):
            return self._cmd_cancel(cmd)
        if cmd in ("color", "textcolor"):
            return self._cmd_color(cmd)
        if cmd == "boxed":
            return self._cmd_boxed()
        if cmd == "operatorname":
            return self._cmd_operatorname(starred)
        if cmd == "substack":
            return self._cmd_substack()
        if cmd in ("abs", "norm"):
            return self._cmd_abs(cmd)
        if cmd in ("mod", "pmod", "pod", "bmod"):
            return self._cmd_mod(cmd)
        if cmd == "tag":
            return self._cmd_tag()
        if cmd == "displaystyle":
            self.displaystyle = True
            return _empty_box()
        if cmd == "textstyle":
            self.displaystyle = False
            return _empty_box()
        if cmd in ("scriptstyle", "scriptscriptstyle"):
            return _empty_box()
        if cmd == "limits":
            self.limits_mode = True
            return _empty_box()
        if cmd == "nolimits":
            self.limits_mode = False
            return _empty_box()
        if cmd == "not":
            return self._cmd_not()
        if cmd in ("mathbin", "mathrel", "mathord", "mathop", "mathinner"):
            return self._render_sub(self._read_group_raw())
        if cmd in _SILENT_COMMANDS:
            if cmd in ("hspace", "vspace", "hphantom", "vphantom", "phantom",
                       "raisebox", "kern", "mkern", "mskip", "hrule"):
                self._read_group_raw()
            if cmd in ("intertext", "shortintertext"):
                raw = self._read_group_raw()
                return _txt(raw, _M_TEXT)
            if cmd in ("label", "ref", "eqref"):
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

    # ── 定界符（left / right / middle / big） ───────────

    def _read_delimiter_char(self) -> str:
        """读取一个定界符（``(`` / ``\\{`` / ``\\langle`` / ``.``）→ 显示字符。

        ★ 修复（review 方向）：``\\{`` / ``\\}`` / ``\\|`` / ``\\.`` / ``\\\\``
        等「反斜杠 + 非字母」定界符此前被当作普通 ``\\`` 处理（``\\left\\{``
        渲染成字面反斜杠）。现按 ``_DELIMITER_MAP`` 的 ``"\\" + 字符`` 键解析，
        未登记时回退该字符本身（如 ``\\{`` → ``{``）。
        """
        while self.i < self.n and self.s[self.i] == " ":
            self.i += 1
        if self.i >= self.n:
            return ""
        c = self.s[self.i]
        if c == "\\":
            if self.i + 1 < self.n and not self.s[self.i + 1].isalpha():
                nxt = self.s[self.i + 1]
                self.i += 2
                return _DELIMITER_MAP.get("\\" + nxt, nxt)
            name = self._peek_cmd(self.i)
            if name:
                self.i += 1 + len(name)
                return _DELIMITER_MAP.get(name, name)
            self.i += 1
            return _DELIMITER_MAP.get(c, c)
        self.i += 1
        return _DELIMITER_MAP.get(c, c)

    def _cmd_left_series(self, cmd: str) -> _Box:
        """``\\left...\\right`` 自动伸缩定界符（``\\right`` 单独出现时退化为定界符）。"""
        if cmd != "left":
            return _txt(self._read_delimiter_char(), _M_SYM)
        left = self._read_delimiter_char()
        body, right = self._scan_until_right()
        inner = self._render_sub(body)
        if right is None:
            return inner
        if not left and not right:
            return inner
        if inner.height > 1:
            return _wrap_delims(inner, left, right)
        line = AnsiLine.of(left, _M_SYM)
        for run in inner.lines[0].runs:
            line.append_run(run)
        line.append(right, _M_SYM)
        return _Box([line])

    def _scan_until_right(self) -> tuple[str, str | None]:
        """扫描到匹配的 ``\\right``（含嵌套 ``\\left``），返回 ``(内容, 右定界符)``。

        未找到匹配时返回 ``(剩余全部内容, None)``——调用方只渲染内容，不丢文本。
        """
        start = self.i
        depth = 0
        p = self.i
        while p < self.n:
            if self.s[p] == "\\":
                if (self.s.startswith("\\left", p)
                        and not self._is_alpha_at(p + 5)):
                    depth += 1
                    p += 5
                    continue
                if (self.s.startswith("\\right", p)
                        and not self._is_alpha_at(p + 6)):
                    if depth == 0:
                        body = self.s[start:p]
                        self.i = p + 6
                        return body, self._read_delimiter_char()
                    depth -= 1
                    p += 6
                    continue
            p += 1
        self.i = self.n
        return self.s[start:], None

    # ── 环境单元格（供 ``_math_env`` 调用） ───────────────

    def _env_cell(self, src: str) -> _Box:
        """环境单元格源码 → 布局块（空单元格为空块）。

        实现在解析主体（``_LatexRenderer``）内，``_math_env`` 仅调用——
        避免 ``_math_env`` → ``_math_latex`` 的反向导入（模块依赖环）。
        ``\\multicolumn{n}{fmt}{content}`` 取内容渲染（终端不合并列宽）。
        """
        text = (src or "").strip()
        if text.startswith("\\multicolumn"):
            from ._math_macros import _read_arg
            pos = len("\\multicolumn")
            for idx in range(3):
                arg, pos = _read_arg(text, pos)
                if idx == 2:
                    text = arg.strip()
        if not text:
            return _empty_box()
        return _LatexRenderer(text, inline=self.inline,
                              macro_state=self.macro_state,
                              preprocess=False).render()

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
        return frac_box(num, den)

    def _cmd_sqrt(self) -> _Box:
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
            nl = AnsiLine.of(("√" if not degree else degree + "√")
                             if idx == 0 else " ", _M_SQRT)
            for run in ln.runs:
                nl.append_run(run)
            lines.append(nl)
        return _Box(lines, baseline=1 + content.baseline)

    def _cmd_binom(self, cmd: str = "binom") -> _Box:
        """二项式系数：块级真二维堆叠（大括号），行内紧凑 ``(n¦k)``。

        ``\\dbinom`` / ``\\tbinom`` 与 ``\\binom`` 共用（终端无字号，
        ``d``/``t`` 前缀仅在行内是否展平上无差别）。
        """
        num = self._render_sub(self._read_group_raw())
        den = self._render_sub(self._read_group_raw())
        if self.inline:
            top = _plain_of(num).strip()
            bot = _plain_of(den).strip()
            return _txt(f"({top}¦{bot})", _M_SYM)
        return _wrap_delims(_vstack([num, den], align="center"), "(", ")")

    def _cmd_not(self) -> _Box:
        """``\\not`` 否定前缀（``\\not=`` → ``≠``、``\\not\\in`` → ``∉``）。

        优先查 ``_NEGATED_SYMBOLS`` 预组合字符表；未登记的关系符回退叠加
        组合长斜线（U+0338），保证任意关系符都能表达「否定」语义。
        """
        while self.i < self.n and self.s[self.i] == " ":
            self.i += 1
        if self.i >= self.n:
            return _txt("\u0338", _M_OP)
        c = self.s[self.i]
        if c == "\\":
            atom = self._parse_command()
            plain = _plain_of(atom).strip()
            return _txt(negate_symbol(plain), _M_OP)
        self.i += 1
        return _txt(negate_symbol(c), _M_OP)

    def _cmd_cancel(self, cmd: str) -> _Box:
        """划除（``\\cancel`` / ``\\bcancel`` / ``\\xcancel`` / ``\\sout``
        / ``\\cancelto``）。

        单行/多行内容统一叠加**真正的删除线**（组合长斜线 U+0336 + 取消配色）
        ——修复前只把内容变暗（视觉上无法区分「划除」与「弱化」）。
        ``\\cancelto{目标}{内容}`` 额外以 ``⤳目标`` 尾随显示目标值。
        """
        if cmd == "cancelto":
            # ``\cancelto{目标}{内容}``：先目标、后内容（LaTeX 参数顺序）
            target_src = self._read_group_raw()
            content = self._render_sub(self._read_group_raw())
            out = strike_through(content, _M_CANCEL)
            target = _plain_of(self._render_sub(target_src)).strip()
            if target:
                return _hjoin([out, _txt("⤳" + target, _M_ARROW)])
            return out
        content = self._render_sub(self._read_group_raw())
        return strike_through(content, _M_CANCEL)

    def _cmd_color(self, cmd: str) -> _Box:
        name = self._read_group_raw().strip()
        content = self._render_sub(self._read_group_raw())
        style = color_style(name)
        lines: list[AnsiLine] = []
        for ln in content.lines:
            nl = AnsiLine()
            for run in ln.runs:
                nl.append_run(Run(run.text, style))
            lines.append(nl)
        return _Box(lines)

    def _cmd_boxed(self, cmd: str = "boxed") -> _Box:
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
        """``\\operatorname{arg\\,max}``：内容按文本语义处理（间距/转义/嵌套）。"""
        raw = self._read_group_raw()
        return _txt(_operatorname_text(raw), _M_FN,
                    kind="limit" if starred else None)

    def _cmd_substack(self) -> _Box:
        raw = self._read_group_raw()
        parts = [_LatexRenderer(seg, inline=self.inline).render()
                 for seg in _split_rows(raw)]
        return _vstack(parts, align="center")

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


def render_math_box(source: str, inline: bool = False,
                    macro_state: MacroState | None = None) -> _Box:
    """LaTeX 源码 → 布局 ``_Box``（行内紧凑 / 块级二维）。

    Args:
        source: LaTeX 源码。
        inline: 行内紧凑排版 / 块级二维排版。
        macro_state: 跨公式共享的宏状态（``\\gdef`` 持久；``None`` 时新建）。
    """
    try:
        return _LatexRenderer(source or "", inline=inline,
                              macro_state=macro_state).render()
    except Exception:
        return _txt(source or "", _M_SYM)


__all__ = ["_Box", "render_math_box", "_LatexRenderer"]
