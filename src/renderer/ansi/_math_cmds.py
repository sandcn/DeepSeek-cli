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
    _M_NOTICE, _M_BRACE, _M_ARROW, _M_HLINE, _M_ACCENT, _M_SUP, _M_SUB,
    color_bg, color_style,
)
from ._math_box import (
    _Box, _txt, _empty_box, _plain_of, _vstack, _hjoin, _wrap_delims,
    frac_box, accent_over, accent_under, repeat_line, join_right,
)
from ._math_letters import to_math_alphabet
from src.renderer.math_symbols.scripts import _SUPERSCRIPT_MAP, _SUBSCRIPT_MAP

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
    "mathbold", "mathsfit",
})

#: 文本语义命令（内容按原文呈现，不解析内部数学命令）
TEXT_SEMANTIC_CMDS: frozenset = frozenset({
    "text", "textrm", "textit", "textbf", "textsf", "texttt",
    "textnormal", "normalfont", "textsc",
    "hbox", "mbox", "textup", "textsl", "textmd", "emph",
})

#: 直立（罗马）字体命令
ROMAN_CMDS: frozenset = frozenset({
    "mathrm", "textrm", "rm", "textnormal", "normalfont", "textup", "textmd",
    "mathup",
})
#: 斜体字体命令
ITALIC_CMDS: frozenset = frozenset({"mathit", "textit", "it", "textsl", "emph"})
#: 粗直立体
BOLD_UPRIGHT_CMDS: frozenset = frozenset({
    "mathbf", "textbf", "bf", "pmb", "bold",
})
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
    "xrightleftarrows": "⇄", "xrightequilibrium": "⇌",
    "xleftequilibrium": "⇋",
    "xtwoheadleftarrow": "↞", "xtwoheadrightarrow": "↠",
    "xtofrom": "⇄",
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
    "Overrightarrow": ("⇒", False),
}

#: 环境内水平线命令 → 线型字符（``_math_env`` 共用）
HLINE_CHARS: dict[str, str] = {
    "hline": "─", "hdashline": "┄", "toprule": "━",
    "midrule": "─", "bottomrule": "━", "cdashline": "┄",
    "cline": "─", "hrule": "─",
}

#: 上下标注命令 → (标注符号, 附着方向)。方向用于 ``_attach_brace`` 决定
#: 标注填入首行（over）还是末行（under）。
_BRACE_MARKS: dict[str, tuple[str, str]] = {
    "overbrace": ("\u23de", "overbrace"),
    "underbrace": ("\u23df", "underbrace"),
    "overbracket": ("\u23b4", "overbrace"),
    "underbracket": ("\u23b5", "underbrace"),
    "overparen": ("\u23dc", "overbrace"),
    "underparen": ("\u23dd", "underbrace"),
    "overgroup": ("\u23e0", "overbrace"),
    "undergroup": ("\u23e1", "underbrace"),
}

#: 上下标记命令 → (标记字符, 方向)：方向 ``over`` 叠加在内容上方、
#: ``under`` 叠加在内容下方。终端无「宽重音」概念，一律整宽铺标记字符。
_MARK_COMMANDS: dict[str, tuple[str, str]] = {
    "widecheck": ("\u02c7", "over"),
    "widebar": ("\u203e", "over"),
    "widetilde": ("\u02dc", "over"),
    "utilde": ("\u02dc", "under"),
    "undertilde": ("\u02dc", "under"),
    "underbar": ("\u2581", "under"),
    "wideparen": ("\u23dc", "over"),
    "overlinesegment": ("\u203e", "over"),
    "underlinesegment": ("\u2581", "under"),
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


#: ``\text`` 系列内容中可还原的转义字符（``\{`` → ``{`` 等）
_TEXT_ESCAPE_CHARS: frozenset = frozenset("\\{}$%&#_ ")


def _unescape_text(seg: str) -> str:
    r"""还原 ``\text`` 内容中的转义字符（``\{``→``{``、``\%``→``%``…）。"""
    out: list[str] = []
    i = 0
    n = len(seg)
    while i < n:
        ch = seg[i]
        if ch == "\\" and i + 1 < n and seg[i + 1] in _TEXT_ESCAPE_CHARS:
            out.append(seg[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


#: ``\text`` 系列内容中可剥离的嵌套文本样式命令（保留其内容文本）
_TEXT_NESTED_CMDS: frozenset = frozenset({
    "text", "textrm", "textit", "textbf", "textsf", "texttt", "textnormal",
    "textsc", "textup", "textsl", "textmd", "emph", "mbox", "hbox",
    "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "operatorname",
})


def _strip_text_commands(seg: str) -> str:
    """剥离 ``\\text`` 内容中的嵌套文本命令，保留其花括号内文本。

    ``\\text{其中 \\textbf{重点}}`` → ``其中 重点``：修复前嵌套命令原样显示
    （含反斜杠与花括号噪音），也不符合 ``\\text``「按原文呈现内容」的语义。
    未登记的转义（``\\{`` 等）与 ``\\\\`` 原样保留，交给后续还原步骤。
    """
    out: list[str] = []
    i = 0
    n = len(seg)
    while i < n:
        ch = seg[i]
        if ch == "\\" and i + 1 < n and seg[i + 1].isalpha():
            j = i + 1
            while j < n and seg[j].isalpha():
                j += 1
            name = seg[i + 1:j]
            if name in _TEXT_NESTED_CMDS:
                k = j
                while k < n and seg[k] == " ":
                    k += 1
                if k < n and seg[k] == "{":
                    depth = 0
                    m = k
                    while m < n:
                        if seg[m] == "{":
                            depth += 1
                        elif seg[m] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        m += 1
                    inner = seg[k + 1:m] if m < n else seg[k + 1:]
                    out.append(_strip_text_commands(inner))
                    i = m + 1
                    continue
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _text_content_lines(raw: str) -> list[str]:
    """``\\text`` 原文 → 行列表。

    ``\\\\``（LaTeX 换行）拆行；嵌套文本命令剥离（保留内容）；文本重音命令
    合成（``\\'a`` → ``á``）；``~`` 还原为空格；转义字符还原；行首尾空白去除
    （LaTeX 在 ``\\text`` 中忽略行首尾空白，保留中间空格）。
    """
    text = _apply_text_accents(_strip_text_commands(raw or ""))
    text = text.replace("\\\\", "\n")
    return [_unescape_text(seg).replace("~", " ").strip() for seg in text.split("\n")]


#: 文本模式重音命令 → 组合字符（U+03xx 组合记号）
_COMBINING_ACCENTS: dict[str, str] = {
    "'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
    "~": "\u0303", "=": "\u0304", ".": "\u0307",
    "u": "\u0306", "v": "\u030c", "H": "\u030b", "r": "\u030a",
    "c": "\u0327", "k": "\u0328", "b": "\u0331", "d": "\u0323",
}

#: 可作重音命令的字符（``\`` 后的非字母字符 + 字母命令）
_TEXT_ACCENT_COMMANDS: frozenset = frozenset(
    set(_COMBINING_ACCENTS) | {"t"}
)


def _apply_text_accents(text: str) -> str:
    """把文本模式重音命令合成为组合字符（``\\'{a}`` → ``á``）。

    ``\\t{oo}``（双字符连接符）在两字符之间插入 U+0361。
    """
    out: list[str] = []
    i = 0
    n = len(text or "")
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in _COMBINING_ACCENTS or nxt == "t":
                j = i + 2
                # 跳过 ``\' {a}`` 之间的空白
                k = j
                while k < n and text[k] == " ":
                    k += 1
                if k < n and text[k] == "{":
                    depth = 0
                    m = k
                    while m < n:
                        if text[m] == "{":
                            depth += 1
                        elif text[m] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        m += 1
                    inner = text[k + 1:m] if m < n else text[k + 1:]
                    i = m + 1
                elif k < n:
                    inner = text[k]
                    i = k + 1
                else:
                    out.append(ch)
                    i += 1
                    continue
                out.append(_accent_text(inner, nxt))
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _accent_text(inner: str, accent: str) -> str:
    """给 ``inner`` 叠加重音组合字符（``t`` 为双字符连接符）。"""
    inner = _resolve_text_letter_cmds(inner)
    if accent == "t":
        if len(inner) >= 2:
            return inner[0] + "\u0361" + inner[1:]
        return inner
    mark = _COMBINING_ACCENTS.get(accent, "")
    if not mark:
        return inner
    return "".join((ch + mark) if not ch.isspace() else ch for ch in inner)


#: 重音命令内部的文本字母命令 → 对应字符（r``\'\i`` → ``í``）
_TEXT_LETTER_CMDS: dict[str, str] = {
    "i": "i", "j": "j", "l": "ł", "L": "Ł",
    "o": "ø", "O": "Ø", "aa": "å", "AA": "Å",
    "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "ss": "ß",
}


def _resolve_text_letter_cmds(text: str) -> str:
    """把文本模式字母命令（``\\i`` / ``\\o`` …）替换为对应字符。"""
    if "\\" not in text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n and text[i + 1].isalpha():
            j = i + 1
            while j < n and text[j].isalpha():
                j += 1
            name = text[i + 1:j]
            mapped = _TEXT_LETTER_CMDS.get(name)
            if mapped is not None:
                out.append(mapped)
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _operatorname_text(raw: str) -> str:
    """``\\operatorname`` 内容 → 纯文本（间距命令转空格、转义还原、嵌套剥离）。

    ``\\operatorname{arg\\,max}`` → ``arg max``；``\\operatorname{lim sup}``
    保留空格；未知命令保留原文（内容不丢）。
    """
    from src.renderer.math_symbols.delimiters import _SPACE_MAP
    text = _apply_text_accents(_strip_text_commands(raw or ""))
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if not nxt.isalpha():
                if nxt in _SPACE_MAP:
                    out.append(_SPACE_MAP[nxt])
                else:
                    out.append(nxt)
                i += 2
                continue
            j = i + 1
            while j < n and text[j].isalpha():
                j += 1
            name = text[i + 1:j]
            if name in _SPACE_MAP:
                out.append(_SPACE_MAP[name])
            elif name in ("text", "mathrm", "operatorname", "textrm", "textit"):
                k = j
                while k < n and text[k] == " ":
                    k += 1
                if k < n and text[k] == "{":
                    depth = 0
                    m = k
                    while m < n:
                        if text[m] == "{":
                            depth += 1
                        elif text[m] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        m += 1
                    out.append(text[k + 1:m] if m < n else text[k + 1:])
                    i = m + 1
                    continue
            else:
                out.append("\\" + name)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _text_segment_box(text: str, style, smallcaps: bool = False) -> _Box:
    """文本语义命令的**分段**内容 → 布局块（保留段内空格、处理换行与重音）。"""
    t = _apply_text_accents(_strip_text_commands(text))
    t = _unescape_text(t).replace("~", " ")
    t = t.replace("\\\\", "\n")
    lines = t.split("\n")
    if smallcaps:
        lines = [x.upper() for x in lines]
    if len(lines) == 1:
        return _txt(lines[0], style)
    return _Box([AnsiLine.of(x, style) for x in lines])


def _script_pair(raw: str) -> tuple[str, str]:
    """``_a^b`` / ``^{b}_{a}`` 原文 → ``(上标原文, 下标原文)``。

    供 ``\\sideset`` 的四角标解析：只识别顶层 ``^`` / ``_``，脚本参数为
    ``{...}``（花括号配对）或单个字符；其余字符忽略。
    """
    sup = sub = ""
    i = 0
    n = len(raw or "")
    while i < n:
        ch = raw[i]
        if ch not in "^_":
            i += 1
            continue
        i += 1
        while i < n and raw[i] == " ":
            i += 1
        if i >= n:
            break
        if raw[i] == "{":
            depth = 0
            start = i + 1
            j = start
            while j < n:
                if raw[j] == "{":
                    depth += 1
                elif raw[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            arg = raw[start:j]
            i = j + 1
        else:
            arg = raw[i]
            i += 1
        if ch == "^":
            sup = arg
        else:
            sub = arg
    return sup, sub


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
        """上下标注符号（``\\overbrace`` / ``\\underbrace`` / ``\\overbracket``
        / ``\\underbracket`` / ``\\overparen`` / ``\\underparen``）。

        块级输出标注符号行 + 内容行（``^{}`` / ``_{}`` 随后填入标注——
        见 ``_attach_brace``）；行内紧凑为「内容 + 标注符号」。
        """
        content = self._render_sub(self._read_group_raw())
        mark, kind = _BRACE_MARKS.get(cmd, ("\u23de", "overbrace"))
        if self.inline:
            line = AnsiLine()
            for run in content.lines[0].runs:
                line.append_run(run)
            line.append(mark, _M_BRACE)
            return _Box([line])
        w = max(1, content.width)
        brace = repeat_line(mark, w, _M_BRACE)
        if kind == "overbrace":
            # 标注行按需插入（``_attach_brace``）——无标注时不预留空行
            lines = [brace] + list(content.lines)
            return _Box(lines, kind=kind, baseline=1 + content.baseline)
        lines = list(content.lines) + [brace]
        return _Box(lines, kind=kind, baseline=content.baseline)

    def _attach_brace(self, base: _Box, sup: _Box | None,
                      sub: _Box | None) -> _Box:
        """把 ``\\overbrace{}^{标注}`` / ``\\underbrace{}_{标注}`` 的标注填入。

        方向匹配的脚本插入/追加标注行（居中）；方向不匹配的脚本（如
        ``\\underbrace{x}^{n}``）以紧凑脚本形式附在内容行尾——修复前直接
        丢弃，内容静默丢失。无标注时不产生任何额外行。
        """
        lines = list(base.lines)
        width = base.width
        over = base.kind in ("overbrace", "overbracket")
        if over:
            content_idx = len(lines) - 1
            primary, secondary = sup, sub
        else:
            content_idx = max(0, len(lines) - 2)
            primary, secondary = sub, sup
        label = _plain_of(primary).strip() if primary is not None else ""
        other = _plain_of(secondary).strip() if secondary is not None else ""
        extra = 0
        if label:
            row = _centered_line(label, width, _M_NOTICE)
            if over:
                lines.insert(0, row)
                extra = 1
            else:
                lines.append(row)
        if other:
            delim = "_{" if over else "^{"
            style = _M_SUB if over else _M_SUP
            lines[content_idx + extra] = join_right(
                lines[content_idx + extra], delim + other + "}", style)
        baseline = base.baseline + (1 if (over and label) else 0)
        return _Box(lines, kind=base.kind, baseline=baseline)

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
            style = _font_style(cmd)
            if "$" in raw:
                return self._render_text_with_math(
                    raw, style, smallcaps=cmd in SMALLCAPS_CMDS)
            lines_text = _text_content_lines(raw)
            if cmd in SMALLCAPS_CMDS:
                lines_text = [t.upper() for t in lines_text]
            if len(lines_text) == 1:
                return _txt(lines_text[0], style)
            return _Box([AnsiLine.of(t, style) for t in lines_text])
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

    # ── 前置上下标（\prescript） ─────────────────────────

    def _cmd_prescript(self, cmd: str = "prescript") -> _Box:
        """``\\prescript{上标}{下标}{主体}`` 前置上下标。

        块级：主体左侧堆叠上下标（同位素记法 ``¹⁴₆C`` 的二维形式）；行内：
        紧凑 ``^{...}_{...}`` 前缀。
        """
        sup_src = self._read_group_raw()
        sub_src = self._read_group_raw()
        base = self._render_sub(self._read_group_raw())
        sup = _plain_of(self._render_sub(sup_src)).strip()
        sub = _plain_of(self._render_sub(sub_src)).strip()
        if self.inline:
            line = AnsiLine()
            if sup:
                line.append("^{" + sup + "}", _M_SUP)
            if sub:
                line.append("_{" + sub + "}", _M_SUB)
            for run in base.lines[0].runs:
                line.append_run(run)
            return _Box([line])
        if not sup and not sub:
            return base
        pre = _vstack([
            _txt(sup, _M_SUP) if sup else _empty_box(),
            _empty_box(),
            _txt(sub, _M_SUB) if sub else _empty_box(),
        ], align="right", baseline=1)
        return _hjoin([pre, base])

    # ── 四角标（\sideset） ───────────────────────────────

    def _cmd_sideset(self, cmd: str = "sideset") -> _Box:
        """``\\sideset{_a^b}{_c^d}\\sum`` 大算符四角标。

        左侧组内容（``_a^b``）渲染在算子左上下、右侧组渲染在右上下；块级为
        真二维（算子垂直居中），行内紧凑为脚本序列。
        """
        left_raw = self._read_group_raw()
        right_raw = self._read_group_raw()
        op = self._parse_atom()
        ls_raw, lb_raw = _script_pair(left_raw)
        rs_raw, rb_raw = _script_pair(right_raw)
        ls = _plain_of(self._render_sub(ls_raw)).strip() if ls_raw else ""
        lb = _plain_of(self._render_sub(lb_raw)).strip() if lb_raw else ""
        rs = _plain_of(self._render_sub(rs_raw)).strip() if rs_raw else ""
        rb = _plain_of(self._render_sub(rb_raw)).strip() if rb_raw else ""
        if self.inline:
            line = AnsiLine()
            if ls:
                line.append("^{" + ls + "}", _M_SUP)
            if lb:
                line.append("_{" + lb + "}", _M_SUB)
            for run in op.lines[0].runs:
                line.append_run(run)
            if rs:
                line.append("^{" + rs + "}", _M_SUP)
            if rb:
                line.append("_{" + rb + "}", _M_SUB)
            return _Box([line])
        if not (ls or lb or rs or rb):
            return op
        left = _vstack([
            _txt(ls, _M_SUP) if ls else _empty_box(),
            _empty_box(),
            _txt(lb, _M_SUB) if lb else _empty_box(),
        ], align="right", baseline=1)
        right = _vstack([
            _txt(rs, _M_SUP) if rs else _empty_box(),
            _empty_box(),
            _txt(rb, _M_SUB) if rb else _empty_box(),
        ], align="left", baseline=1)
        return _hjoin([left, op, right])

    # ── 宽标记（widecheck / widebar / utilde / underbar） ─

    def _cmd_mark(self, cmd: str) -> _Box:
        """整宽上下标记（终端无「宽重音」概念，按内容宽度铺标记字符）。"""
        content = self._render_sub(self._read_group_raw())
        mark, direction = _MARK_COMMANDS.get(cmd, ("\u203e", "over"))
        if direction == "over":
            return accent_over(content, mark, _M_ACCENT)
        return accent_under(content, mark, _M_ACCENT)

    # ── 文本上下标（\textsuperscript / \textsubscript） ──

    def _cmd_textscript(self, cmd: str) -> _Box:
        """文本上下标：内容可 Unicode 化时直接转换，否则紧凑脚本形式。"""
        raw = self._read_group_raw()
        is_sup = cmd == "textsuperscript"
        mapping = _SUPERSCRIPT_MAP if is_sup else _SUBSCRIPT_MAP
        style = _M_SUP if is_sup else _M_SUB
        if raw and all(ch in mapping or ch.isspace() for ch in raw):
            return _txt("".join(mapping.get(ch, ch) for ch in raw), style)
        return _txt(("^{" if is_sup else "_{") + raw + "}", style)

    # ── 原文命令（\verb） ────────────────────────────────

    def _cmd_verb(self, cmd: str = "verb") -> _Box:
        """``\\verb|原文|`` / ``\\verb*|原文|``：按代码样式原样呈现。"""
        if self.i < self.n and self.s[self.i] == "*":
            self.i += 1
        if self.i >= self.n:
            return _empty_box()
        delim = self.s[self.i]
        self.i += 1
        end = self.s.find(delim, self.i)
        if end < 0:
            text = self.s[self.i:]
            self.i = self.n
        else:
            text = self.s[self.i:end]
            self.i = end + 1
        return _txt(text, _M_CODE)

    # ── 透明包裹命令（内容照常渲染） ─────────────────────

    def _cmd_wrap(self, cmd: str) -> _Box:
        """忽略排版语义、只保留内容的命令（``\\smash`` / ``\\mathclap``…）。

        终端无「盒子重叠/裁剪」概念，直接渲染内容（内容不丢）。
        """
        return self._render_sub(self._read_group_raw())

    # ── 取整定界（\ceil / \floor） ──────────────────────

    def _cmd_brackets(self, cmd: str) -> _Box:
        """``\\ceil{x}`` → ``⌈x⌉``；``\\floor{x}`` → ``⌊x⌋``（多维感知）。"""
        content = self._render_sub(self._read_group_raw())
        if cmd == "ceil":
            return _wrap_delims(content, "⌈", "⌉")
        return _wrap_delims(content, "⌊", "⌋")

    # ── 小分数（\nicefrac / \sfrac） ────────────────────

    def _cmd_nicefrac(self, cmd: str = "nicefrac") -> _Box:
        """``\\nicefrac{a}{b}``：行内紧凑 ``a⁄b``，块级与 ``\\frac`` 相同。"""
        num = self._render_sub(self._read_group_raw())
        den = self._render_sub(self._read_group_raw())
        if self.inline:
            ns = _plain_of(num).strip()
            ds = _plain_of(den).strip()
            if _needs_paren(ns):
                ns = "(" + ns + ")"
            if _needs_paren(ds):
                ds = "(" + ds + ")"
            return _txt(ns + "⁄" + ds, _M_SYM)
        return frac_box(num, den)

    # ── 上下划线（overline / underline，支持多行内容） ──

    def _cmd_overline(self, cmd: str) -> _Box:
        """上划线 / 下划线（下划线使用 ``▁`` 实心下横，比 ``_`` 更醒目）。"""
        content = self._render_sub(self._read_group_raw())
        if cmd == "overline":
            return accent_over(content, "\u203e", _M_ACCENT)
        return accent_under(content, "\u2581", _M_ACCENT)

    # ── 重音（hat / tilde / vec / dot …） ────────────────

    def _cmd_accent(self, cmd: str) -> _Box:
        """重音命令：单行内容追加组合字符；多行内容在上方叠加重音行。"""
        from src.renderer.math_symbols.misc import _ACCENT_MAP
        content = self._render_sub(self._read_group_raw())
        mark = _ACCENT_MAP.get(cmd, "")
        if not mark:
            return content
        if content.height != 1:
            wide = {
                "vec": "→", "widehat": "^", "bar": "‾", "tilde": "~",
                "dddot": "⋯", "ddddot": "⋯",
            }.get(cmd, mark)
            return accent_over(content, wide, _M_ACCENT)
        line = AnsiLine()
        for run in content.lines[0].runs:
            line.append_run(run)
        line.append(mark, _M_ACCENT)
        return _Box([line])

    # ── 文本模式重音（\'{a} / \u{a} / \c{c} …） ──────────

    def _cmd_text_accent_char(self, ch: str) -> _Box:
        """``\\'{a}`` / ``\\^{a}`` 等（``\\`` + 非字母重音字符）。"""
        inner = self._read_group_raw()
        return _txt(_accent_text(inner, ch), _M_TEXT)

    def _cmd_text_accent(self, cmd: str) -> _Box:
        """``\\u{a}`` / ``\\c{c}`` / ``\\H{o}`` 等文本重音字母命令。"""
        inner = self._read_group_raw()
        return _txt(_accent_text(inner, cmd), _M_TEXT)

    # ── 文本内容中的内联数学（``\text{...$x^2$...}``） ──

    def _render_text_with_math(self, raw: str, style, smallcaps: bool = False) -> _Box:
        """文本语义命令内容：``$…$`` 片段按数学渲染，其余按文本样式呈现。"""
        parts: list[_Box] = []
        seg: list[str] = []
        raw = (raw or "").strip()
        i = 0
        n = len(raw)
        while i < n:
            ch = raw[i]
            if ch == "$":
                delim = "$$" if raw.startswith("$$", i) else "$"
                end = raw.find(delim, i + len(delim))
                if delim == "$" and end < 0:
                    seg.append(ch)
                    i += 1
                    continue
                if end > i:
                    if seg:
                        parts.append(_text_segment_box("".join(seg), style,
                                                       smallcaps))
                        seg = []
                    math_src = raw[i + len(delim):end]
                    if delim == "$$":
                        parts.append(_txt(" ", style))
                    parts.append(self._render_sub(math_src))
                    if delim == "$$":
                        parts.append(_txt(" ", style))
                    i = end + len(delim)
                    continue
            seg.append(ch)
            i += 1
        if seg or not parts:
            parts.append(_text_segment_box("".join(seg), style, smallcaps))
        return _hjoin(parts)


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
    for cmd in _BRACE_MARKS:
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
    # ── 第二批扩展（前后上下标 / 四角标 / 宽标记 / 文本脚本 / 原文） ──
    table["prescript"] = "_cmd_prescript"
    table["sideset"] = "_cmd_sideset"
    for cmd in _MARK_COMMANDS:
        table[cmd] = "_cmd_mark"
    for cmd in ("textsuperscript", "textsubscript"):
        table[cmd] = "_cmd_textscript"
    table["verb"] = "_cmd_verb"
    for cmd in ("dbinom", "tbinom"):
        table[cmd] = "_cmd_binom"
    for cmd in ("smash", "mathclap", "mathllap", "mathrlap", "clap", "llap",
                "rlap", "vcenter", "mathmakebox"):
        table[cmd] = "_cmd_wrap"
    for cmd in ("ceil", "floor"):
        table[cmd] = "_cmd_brackets"
    for cmd in ("nicefrac", "sfrac"):
        table[cmd] = "_cmd_nicefrac"
    # ── 文本模式重音（\u{a} / \v{a} / \c{c} / \H{o} / \r{a} / \k{a} /
    #    \b{a} / \d{a} / \t{oo}）──
    for cmd in ("u", "v", "H", "r", "c", "k", "b", "d", "t"):
        table[cmd] = "_cmd_text_accent"
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
    "_BRACE_MARKS", "_MARK_COMMANDS",
]
