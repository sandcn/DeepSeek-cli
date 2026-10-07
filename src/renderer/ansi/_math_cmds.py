"""_math_cmds — LaTeX 扩展命令（叠加标注 / 花括号 / 字体族 / 着色 / 占位…）。

以 mixin 形式挂在 ``_math_latex._LatexRenderer`` 上：主体只保留解析骨架与
命令分派，具体命令实现集中在本模块，避免解析器随命令数量膨胀。

扩展方式：在 ``COMMAND_HANDLERS`` 中登记「命令名 → 本 mixin 的方法名」即可
（方法统一签名 ``(self, cmd)``），新增命令无需改动解析主体（开闭原则）。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine

from ._math_style import (
    _M_SYM, _M_TEXT, _M_TEXT_ROMAN, _M_TEXT_BOLD, _M_MATH_BOLD, _M_CODE,
    _M_NOTICE, _M_BRACE, _M_ARROW, _M_HLINE, _M_ACCENT, color_bg, color_style,
)
from ._math_box import (
    _Box, _txt, _empty_box, _plain_of, _vstack, _wrap_delims,
    frac_box, accent_over, accent_under, repeat_line,
)
from ._math_letters import to_math_alphabet

# ── 命令分组常量 ───────────────────────────────────────────

#: Unicode 数学字母族命令（内容为 ASCII 字母时转换为数学字母字形）
UNICODE_ALPHABET_CMDS: frozenset = frozenset({
    "mathbb", "Bbb", "mathds",
    "mathcal", "mathscr", "EuScript", "mathpzc", "cal",
    "mathfrak", "frak",
    "mathbffrak",
    "mathbf", "boldsymbol", "bm", "mathbfit",
    "mathboldsf", "mathsf", "mathtt",
    "mathit", "mathnormal",
})

#: 文本语义命令（内容按原文呈现，不解析内部数学命令）
TEXT_SEMANTIC_CMDS: frozenset = frozenset({
    "text", "textrm", "textit", "textbf", "textsf", "texttt",
    "textnormal", "normalfont", "textsc",
})

#: 直立（罗马）字体命令
ROMAN_CMDS: frozenset = frozenset({"mathrm", "textrm", "rm", "textnormal", "normalfont"})
#: 斜体字体命令
ITALIC_CMDS: frozenset = frozenset({"mathit", "textit", "it"})
#: 粗直立体
BOLD_UPRIGHT_CMDS: frozenset = frozenset({"mathbf", "textbf", "bf"})
#: 粗斜体
BOLD_ITALIC_CMDS: frozenset = frozenset({"boldsymbol", "bm", "mathbfit", "mathboldsf"})
#: 无衬线
SANS_CMDS: frozenset = frozenset({"mathsf", "textsf", "sf"})
#: 等宽
MONO_CMDS: frozenset = frozenset({"mathtt", "texttt", "tt"})
#: 小型大写
SMALLCAPS_CMDS: frozenset = frozenset({"textsc", "sc"})

#: ``\xrightarrow`` 系列命令 → 箭头符号
XARROW_SYMBOLS: dict[str, str] = {
    "xrightarrow": "⟶", "xleftarrow": "⟵",
    "xRightarrow": "⟹", "xLeftarrow": "⟸",
    "xLeftrightarrow": "⟺", "xleftrightarrow": "⟷",
    "xmapsto": "⟼", "xlongequal": "＝",
    "xhookrightarrow": "⤳", "xhookleftarrow": "↩",
    "xrightleftharpoons": "⇌", "xleftrightharpoons": "⇋",
    "xrightharpoonup": "⇀", "xrightharpoondown": "⇁",
    "xleftharpoonup": "↼", "xleftharpoondown": "↽",
}

#: ``\overrightarrow`` 系列 → (方向标记, 是否在内容下方)
VECTOR_MARKS: dict[str, tuple[str, bool]] = {
    "overrightarrow": ("→", False),
    "overleftarrow": ("←", False),
    "overleftrightarrow": ("↔", False),
    "underrightarrow": ("→", True),
    "underleftarrow": ("←", True),
    "underleftrightarrow": ("↔", True),
    "overrightharpoon": ("⇀", False),
    "overleftharpoon": ("↼", False),
}

#: 环境内水平线命令 → 线型字符（``_math_env`` 共用）
HLINE_CHARS: dict[str, str] = {
    "hline": "─", "hdashline": "┄", "toprule": "━",
    "midrule": "─", "bottomrule": "━", "cdashline": "┄",
    "cline": "─", "hrule": "─",
}

#: 环境外水平线的默认宽度（字符数）
_BARE_HLINE_WIDTH = 20

#: 长度单位 → 字符数换算（``\\rule`` / ``\\rule{2em}{1pt}``）
_LENGTH_UNITS: dict[str, float] = {
    "em": 1.0, "ex": 0.5, "pt": 0.12, "px": 0.12,
    "mm": 0.35, "cm": 3.5, "in": 8.8, "bp": 0.12, "pc": 1.45,
}


def _length_to_chars(spec: str, default: int = 1) -> int:
    """长度参数（如 ``2em`` / ``10pt`` / ``.5\\textwidth``）→ 字符数。"""
    s = (spec or "").strip().lstrip("+-")
    digits = ""
    for ch in s:
        if ch.isdigit() or (ch == "." and "." not in digits):
            digits += ch
        else:
            break
    if not digits:
        return default
    try:
        value = float(digits)
    except ValueError:
        return default
    unit = s[len(digits):].strip().lower()
    unit = "".join(c for c in unit if c.isalpha())
    factor = _LENGTH_UNITS.get(unit, 1.0)
    return max(0, int(round(value * factor)))


def _centered_line(text: str, width: int, style) -> AnsiLine:
    """在给定宽度内居中放置文本（左侧留白 + 文本）。"""
    if not text:
        return AnsiLine()
    left = max(0, (width - len(text)) // 2)
    return AnsiLine.of(" " * left + text, style)


def _restyle_box(box: _Box, style: Style,
                 force_fg=None, force_bg=None) -> _Box:
    """整体重着色（在既有 run 样式上合并 ``style``；``force_*`` 强制覆盖）。"""
    lines: list[AnsiLine] = []
    for ln in box.lines:
        nl = AnsiLine()
        for run in ln.runs:
            base = run.style if run.style is not None else Style()
            merged = base.merge(style)
            if force_fg is not None or force_bg is not None:
                merged = Style(
                    fg=force_fg if force_fg is not None else merged.fg,
                    bg=force_bg if force_bg is not None else merged.bg,
                    bold=merged.bold, italic=merged.italic,
                    dim=merged.dim, underline=merged.underline,
                )
            nl.append(run.text, merged)
        lines.append(nl)
    return _Box(lines, kind=box.kind, baseline=box.baseline)


class _MathCommandMixin:
    """扩展命令实现（由 ``_LatexRenderer`` 继承）。"""

    # ── 参数读取辅助 ─────────────────────────────────────

    def _read_optional_raw(self) -> str:
        """读取可选参数 ``[...]``（不存在时返回空串，不消费输入）。"""
        while self.i < self.n and self.s[self.i] == " ":
            self.i += 1
        if self.i >= self.n or self.s[self.i] != "[":
            return ""
        end = self.s.find("]", self.i)
        if end < 0:
            return ""
        value = self.s[self.i + 1:end]
        self.i = end + 1
        return value

    # ── 上下叠加标注（overset / underset / stackrel） ────

    def _cmd_stacked(self, cmd: str) -> _Box:
        """``\\overset{上}{主体}`` / ``\\underset{下}{主体}`` / ``\\stackrel``。

        块级：真上下堆叠（标注置于主体上方/下方，居中）；行内：标注以脚本
        形式附在主体右侧（终端单行约束下的紧凑近似）。
        """
        label_src = self._read_group_raw()
        base = self._render_sub(self._read_group_raw())
        label = _plain_of(self._render_sub(label_src)).strip()
        if self.inline:
            if not label:
                return base
            line = AnsiLine()
            for run in base.lines[0].runs:
                line.append_run(run)
            self._append_script(line, _txt(label), is_sup=(cmd != "underset"))
            return _Box([line])
        if not label:
            return base
        lab = _txt(label, _M_NOTICE)
        if cmd == "underset":
            return _vstack([base, lab], align="center", baseline=base.baseline)
        return _vstack([lab, base], align="center",
                       baseline=lab.height + base.baseline)

    # ── 上下花括号（overbrace / underbrace） ─────────────

    def _cmd_brace(self, cmd: str) -> _Box:
        """``\\overbrace{x+y}^{n}`` / ``\\underbrace{x+y}_{n}``。

        块级输出花括号行 + 标注行（标注由随后的 ``^{}`` / ``_{}`` 填入——
        见 ``_attach_brace``）；行内紧凑为「内容 + 花括号符号」。
        """
        content = self._render_sub(self._read_group_raw())
        kind = "overbrace" if cmd == "overbrace" else "underbrace"
        mark = "⏞" if kind == "overbrace" else "⏟"
        if self.inline:
            line = AnsiLine()
            for run in content.lines[0].runs:
                line.append_run(run)
            line.append(mark, _M_BRACE)
            return _Box([line])
        w = max(1, content.width)
        brace = repeat_line(mark, w, _M_BRACE)
        blank = AnsiLine()
        if kind == "overbrace":
            lines = [blank, brace] + list(content.lines)
            return _Box(lines, kind=kind, baseline=2 + content.baseline)
        lines = list(content.lines) + [brace, blank]
        return _Box(lines, kind=kind, baseline=content.baseline)

    def _attach_brace(self, base: _Box, sup: _Box | None,
                      sub: _Box | None) -> _Box:
        """把 ``\\overbrace{}^{标注}`` / ``\\underbrace{}_{标注}`` 的标注填入。"""
        lines = list(base.lines)
        width = base.width
        if base.kind == "overbrace" and sup is not None:
            label = _plain_of(sup).strip()
            if label:
                lines[0] = _centered_line(label, width, _M_NOTICE)
        elif base.kind == "underbrace" and sub is not None:
            label = _plain_of(sub).strip()
            if label:
                lines[-1] = _centered_line(label, width, _M_NOTICE)
        return _Box(lines, kind=base.kind, baseline=base.baseline)

    # ── 着色盒（colorbox / fcolorbox） ───────────────────

    def _cmd_colorbox(self, cmd: str) -> _Box:
        """``\\colorbox{颜色}{内容}`` / ``\\fcolorbox{边框色}{底色}{内容}``。"""
        frame = ""
        if cmd == "fcolorbox":
            frame = self._read_group_raw().strip()
        bg_name = self._read_group_raw().strip()
        content = self._render_sub(self._read_group_raw())
        bg = color_bg(bg_name)
        if cmd == "fcolorbox":
            frame_fg = color_style(frame).fg
            out = _restyle_box(content, Style(bg=bg) if bg is not None else Style(),
                               force_fg=frame_fg)
            lines: list[AnsiLine] = []
            for ln in out.lines:
                nl = AnsiLine.of("▌", Style(fg=frame_fg, bg=bg))
                for run in ln.runs:
                    nl.append_run(run)
                nl.append("▌", Style(fg=frame_fg, bg=bg))
                lines.append(nl)
            return _Box(lines, baseline=out.baseline)
        if bg is None:
            return content
        return _restyle_box(content, Style(bg=bg))

    # ── 占位（phantom 系列） ─────────────────────────────

    def _cmd_phantom(self, cmd: str) -> _Box:
        """``\\phantom`` 保留宽高、``\\hphantom`` 只保留宽、``\\vphantom`` 只保留高。"""
        content = self._render_sub(self._read_group_raw())
        w = content.width
        if cmd == "vphantom":
            return _Box([AnsiLine() for _ in range(max(1, content.height))])
        if cmd == "hphantom":
            return _Box([AnsiLine.of(" " * w)]) if w else _empty_box()
        lines = [AnsiLine.of(" " * w) if w else AnsiLine()
                 for _ in range(max(1, content.height))]
        return _Box(lines, baseline=content.baseline)

    # ── 尺寸线（rule） ───────────────────────────────────

    def _cmd_rule(self, cmd: str = "rule") -> _Box:
        """``\\rule{宽度}{高度}`` → 实心条（宽度按 em/pt 换算字符数）。"""
        width_spec = self._read_group_raw()
        height_spec = self._read_group_raw()
        n = _length_to_chars(width_spec, default=1)
        rows = max(1, min(3, _length_to_chars(height_spec, default=1)))
        if n <= 0:
            return _empty_box()
        bar = "▬" * n
        return _Box([AnsiLine.of(bar, _M_SYM) for _ in range(rows)])

    # ── 通用分数（genfrac） ──────────────────────────────

    def _cmd_genfrac(self, cmd: str = "genfrac") -> _Box:
        """``\\genfrac{(}{)}{0pt}{}{分子}{分母}`` → 带定界符的分数。"""
        left = self._read_group_raw().strip()
        right = self._read_group_raw().strip()
        self._read_group_raw()   # 字号（终端无字号概念，忽略）
        self._read_group_raw()   # 样式（同上）
        num_src = self._read_group_raw()
        den_src = self._read_group_raw()
        num = self._render_sub(num_src)
        den = self._render_sub(den_src)
        if self.inline:
            ns = _plain_of(num)
            ds = _plain_of(den)
            if _needs_paren(ns):
                ns = "(" + ns + ")"
            if _needs_paren(ds):
                ds = "(" + ds + ")"
            return _txt(f"{left}{ns}⁄{ds}{right}", _M_SYM)
        box = frac_box(num, den)
        if left or right:
            return _wrap_delims(box, left, right)
        return box

    def _cmd_framebox(self, cmd: str = "framebox") -> _Box:
        """``\\framebox{内容}`` → 同 ``\\boxed``（可选 ``[宽度]`` 参数忽略）。"""
        self._read_optional_raw()
        self._read_optional_raw()
        return self._cmd_boxed()

    # ── 字体族（mathrm / mathbf / mathcal / mathbb …） ───

    def _cmd_font(self, cmd: str) -> _Box:
        """字体族命令：Unicode 字母族优先，其次按语义样式近似。

        - 文本语义命令（``\\text``/``\\textrm``…）内容按原文呈现；
        - 数学字体命令（``\\mathbf{x^2}``）内容仍按数学解析后再着色；
        - ``\\mathbb{R}`` / ``\\mathcal{F}`` 等转换为 Unicode 数学字母字形
          （内容非 ASCII 字母时回退样式渲染，内容不丢）。
        """
        raw = self._read_group_raw()
        if cmd in UNICODE_ALPHABET_CMDS:
            converted = to_math_alphabet(raw, cmd)
            if converted is not None:
                return _txt(converted, _alphabet_style(cmd))
        if cmd in TEXT_SEMANTIC_CMDS:
            text = raw
            if cmd in SMALLCAPS_CMDS:
                text = text.upper()
            return _txt(text, _font_style(cmd))
        content = self._render_sub(raw)
        return _restyle_box(content, _font_style(cmd))

    # ── 环境内 / 环境外水平线 ────────────────────────────

    def _cmd_hline(self, cmd: str) -> _Box:
        """水平线命令（环境内由 ``_math_env`` 处理；环境外渲染短横线）。"""
        if cmd == "cline":
            self._read_group_raw()
        ch = HLINE_CHARS.get(cmd, "─")
        return _Box([repeat_line(ch, _BARE_HLINE_WIDTH, _M_HLINE)])

    # ── 向量箭头（over/underrightarrow 等） ──────────────

    def _cmd_vector(self, cmd: str) -> _Box:
        """``\\overrightarrow{AB}`` 等：单行内容叠加箭头符号，多行内容加上方箭头行。"""
        content = self._render_sub(self._read_group_raw())
        mark, below = VECTOR_MARKS.get(cmd, ("→", False))
        if content.height > 1:
            if below:
                return accent_under(content, mark, _M_ARROW)
            return accent_over(content, mark, _M_ARROW)
        line = AnsiLine()
        for run in content.lines[0].runs:
            line.append_run(run)
        line.append(mark, _M_ARROW)
        return _Box([line])

    # ── 带标注箭头（xrightarrow[下]{上}） ────────────────

    def _cmd_xarrow(self, cmd: str) -> _Box:
        """``\\xrightarrow[下]{上}``：块级上下堆叠标注，行内紧凑呈现。"""
        below_src = self._read_optional_raw()
        above = _plain_of(self._render_sub(self._read_group_raw())).strip()
        symbol = XARROW_SYMBOLS.get(cmd, "⟶")
        below = _plain_of(self._render_sub(below_src)).strip() if below_src else ""
        if self.inline:
            text = f"──{above}──▶" if above else "──▶"
            if below:
                text += f"_{{{below}}}"
            return _txt(text, _M_ARROW)
        parts: list[_Box] = []
        if above:
            parts.append(_txt(above, _M_NOTICE))
        baseline = len(parts)
        parts.append(_txt(f"──{symbol}", _M_ARROW))
        if below:
            parts.append(_txt(below, _M_NOTICE))
        return _vstack(parts, align="center", baseline=baseline)

    # ── 上下划线（overline / underline，支持多行内容） ──

    def _cmd_overline(self, cmd: str) -> _Box:
        content = self._render_sub(self._read_group_raw())
        if cmd == "overline":
            return accent_over(content, "‾", _M_ACCENT)
        return accent_under(content, "_", _M_ACCENT)

    # ── 重音（hat / tilde / vec / dot …） ────────────────

    def _cmd_accent(self, cmd: str) -> _Box:
        """重音命令：单行内容追加组合字符；多行内容在上方叠加重音行。"""
        from src.renderer.math_symbols.misc import _ACCENT_MAP
        content = self._render_sub(self._read_group_raw())
        mark = _ACCENT_MAP.get(cmd, "")
        if not mark:
            return content
        if content.height != 1:
            wide = {"vec": "→", "widehat": "^", "bar": "‾", "tilde": "~"}.get(cmd, mark)
            return accent_over(content, wide, _M_ACCENT)
        line = AnsiLine()
        for run in content.lines[0].runs:
            line.append_run(run)
        line.append(mark, _M_ACCENT)
        return _Box([line])


def _needs_paren(text: str) -> bool:
    """分数紧凑形式是否需要加括号（含顶层运算符时）。"""
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "+-−=<>&|":
            return True
    return False


def _font_style(cmd: str) -> Style:
    """字体命令 → 终端样式近似。"""
    if cmd in ROMAN_CMDS:
        return _M_TEXT_ROMAN
    if cmd in ITALIC_CMDS:
        return _M_TEXT
    if cmd in BOLD_UPRIGHT_CMDS:
        return _M_MATH_BOLD
    if cmd in BOLD_ITALIC_CMDS:
        return _M_TEXT_BOLD
    if cmd in MONO_CMDS:
        return _M_CODE
    if cmd in SANS_CMDS or cmd in SMALLCAPS_CMDS:
        return _M_TEXT_ROMAN
    return _M_TEXT


def _alphabet_style(cmd: str) -> Style:
    """Unicode 字母族命令 → 文字色（字形已表达字重/字形，不再叠加 italic）。"""
    if cmd in ("mathbf",):
        return _M_MATH_BOLD
    if cmd in BOLD_ITALIC_CMDS:
        return _M_MATH_BOLD
    return _M_SYM


#: 扩展命令 → mixin 方法名（解析主体据此分派；新增命令只改本表）
COMMAND_HANDLERS: dict[str, str] = {}

#: 全部字体族命令（分组常量并集——新增字体族只改上方常量，不会漏注册）
_FONT_COMMANDS: frozenset = frozenset(
    set(TEXT_SEMANTIC_CMDS) | set(ROMAN_CMDS) | set(ITALIC_CMDS)
    | set(BOLD_UPRIGHT_CMDS) | set(BOLD_ITALIC_CMDS) | set(SANS_CMDS)
    | set(MONO_CMDS) | set(SMALLCAPS_CMDS) | set(UNICODE_ALPHABET_CMDS)
)


def _register_commands() -> dict[str, str]:
    table: dict[str, str] = {}
    for cmd in ("overset", "underset", "stackrel"):
        table[cmd] = "_cmd_stacked"
    for cmd in ("overbrace", "underbrace"):
        table[cmd] = "_cmd_brace"
    for cmd in ("colorbox", "fcolorbox"):
        table[cmd] = "_cmd_colorbox"
    for cmd in ("phantom", "hphantom", "vphantom"):
        table[cmd] = "_cmd_phantom"
    table["rule"] = "_cmd_rule"
    table["genfrac"] = "_cmd_genfrac"
    table["fbox"] = "_cmd_boxed"
    table["framebox"] = "_cmd_framebox"
    for cmd in _FONT_COMMANDS:
        table[cmd] = "_cmd_font"
    for cmd in HLINE_CHARS:
        table[cmd] = "_cmd_hline"
    for cmd in VECTOR_MARKS:
        table[cmd] = "_cmd_vector"
    for cmd in XARROW_SYMBOLS:
        table[cmd] = "_cmd_xarrow"
    table["overline"] = "_cmd_overline"
    table["underline"] = "_cmd_overline"
    return table


COMMAND_HANDLERS.update(_register_commands())


def register_math_command(cmd: str, handler: str) -> None:
    """注册扩展数学命令（插件扩展点：``handler`` 为 mixin 上的方法名）。"""
    if cmd and handler:
        COMMAND_HANDLERS[cmd] = handler


__all__ = [
    "COMMAND_HANDLERS", "register_math_command", "HLINE_CHARS",
    "XARROW_SYMBOLS", "VECTOR_MARKS", "UNICODE_ALPHABET_CMDS",
    "TEXT_SEMANTIC_CMDS", "_MathCommandMixin", "_length_to_chars",
]
