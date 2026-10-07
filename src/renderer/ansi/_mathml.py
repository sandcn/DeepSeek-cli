"""_mathml — MathML → LaTeX → 终端二维排版。

HTML ``<math>`` 块的内容是 Presentation MathML。终端无法直接排版 MathML
树，但可以**先转换为等价 LaTeX 源码**，再复用既有 LaTeX 排版器
（``_math_latex.render_math_box``，支持分数堆叠 / 根式 / 大算符上下限 /
矩阵 / 自动伸缩定界符等）。

设计要点：

  - **宽容解析**：用 ``html.parser.HTMLParser`` 建轻量节点树（容错未闭合
    标签、未定义实体、命名空间前缀），不依赖第三方库；
  - **注册表分派**：``_HANDLERS`` 为「元素名 → 转换函数」，新增 MathML 元素
    只需登记一个函数（开闭原则），不改动遍历/渲染逻辑；
  - **失败回退**：无法解析或无有效内容时返回 ``None``，调用方保留原文显示
    （内容永不丢失）。

支持的元素（Presentation MathML 常用子集）：mi / mn / mo / mtext / ms /
mspace / mrow / mfrac / msqrt / mroot / msup / msub / msubsup / munder /
mover / munderover / mfenced / mtable(-mtr/-mtd) / mstyle / mpadded /
mphantom / menclose / maction / semantics / mmultiscripts / merror。
"""

from __future__ import annotations

from html.parser import HTMLParser

from ._math_latex import render_math_box

#: 解析/转换最大递归深度（防异常输入）
_MAX_DEPTH = 48


class _Node:
    """轻量 MathML 节点（标签名小写、属性名小写、子节点 + 文本 + 尾随文本）。"""

    __slots__ = ("tag", "attrs", "children", "text", "tail")

    def __init__(self, tag: str, attrs: dict) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list["_Node"] = []
        self.text = ""
        self.tail = ""


#: 自闭合/无子节点语义的元素（不压栈）
_VOID_TAGS = frozenset({
    "mspace", "none", "mprescripts", "annotation", "annotation-xml",
})


def _norm_tag(tag: str) -> str:
    """标签名规范化：小写 + 去掉命名空间前缀（``m:mi`` → ``mi``）。"""
    name = (tag or "").strip().lower()
    if ":" in name:
        name = name.split(":")[-1]
    return name


class _TreeBuilder(HTMLParser):
    """MathML 片段 → 轻量节点树（容错解析）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("math", {})
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag, attrs) -> None:
        node = _Node(_norm_tag(tag),
                     {str(k).lower(): (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs) -> None:
        node = _Node(_norm_tag(tag),
                     {str(k).lower(): (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag) -> None:
        name = _norm_tag(tag)
        for idx in range(len(self._stack) - 1, 0, -1):
            if self._stack[idx].tag == name:
                del self._stack[idx:]
                return

    def handle_data(self, data) -> None:
        node = self._stack[-1]
        if node.children:
            node.children[-1].tail += data
        else:
            node.text += data


def parse_mathml(source: str) -> _Node | None:
    """MathML 源码 → 节点树（无有效内容返回 ``None``）。"""
    src = (source or "").strip()
    if "<" not in src:
        return None
    builder = _TreeBuilder()
    try:
        builder.feed(src)
        builder.close()
    except Exception:
        return None
    root = builder.root
    if not root.children and not root.text.strip():
        return None
    return root


# ── 文本与 LaTeX 转义 ─────────────────────────────────────

#: 普通数学文本中的 LaTeX 特殊字符 → 安全写法
_MATH_TEXT_ESCAPE = {
    "\\": "\\backslash ",
    "{": "\\{",
    "}": "\\}",
    "$": "\\$",
    "%": "\\%",
    "&": "\\&",
    "#": "\\#",
}


def _escape_math_text(text: str) -> str:
    out: list[str] = []
    for ch in (text or ""):
        out.append(_MATH_TEXT_ESCAPE.get(ch, ch))
    return "".join(out)


#: ``\text{...}`` 内容中的转义（``~`` 表示空格，终端的数学文本命令会再还原）
_TEXT_ESCAPE = {"\\": "\\backslash ", "{": "\\{", "}": "\\}", "$": "\\$"}


def _escape_text(text: str) -> str:
    out: list[str] = []
    for ch in (text or ""):
        out.append(_TEXT_ESCAPE.get(ch, ch))
    return "".join(out)


def _raw_text(node: _Node) -> str:
    """节点及其子孙的全部文本（不含结构）。"""
    parts = [node.text]
    for child in node.children:
        parts.append(_raw_text(child))
        parts.append(child.tail)
    return "".join(parts)


# ── 运算符映射 ────────────────────────────────────────────

#: MathML 运算符字符 → LaTeX 命令（未登记者按原文渲染）
_OPERATOR_LATEX: dict[str, str] = {
    "∑": "\\sum", "∏": "\\prod", "∐": "\\coprod",
    "∫": "\\int", "∮": "\\oint", "∬": "\\iint", "∭": "\\iiint",
    "⋃": "\\bigcup", "⋂": "\\bigcap", "⋁": "\\bigvee", "⋀": "\\bigwedge",
    "⨁": "\\bigoplus", "⨂": "\\bigotimes", "⨆": "\\bigsqcup", "⨄": "\\biguplus",
    "≤": "\\le", "≥": "\\ge", "≠": "\\ne", "≈": "\\approx",
    "≡": "\\equiv", "∼": "\\sim", "≅": "\\cong", "≃": "\\simeq",
    "≪": "\\ll", "≫": "\\gg", "∝": "\\propto",
    "×": "\\times", "÷": "\\div", "±": "\\pm", "∓": "\\mp",
    "⋅": "\\cdot", "·": "\\cdot", "∗": "\\ast", "⋆": "\\star",
    "⊗": "\\otimes", "⊕": "\\oplus", "⊙": "\\odot",
    "∧": "\\wedge", "∨": "\\vee", "∩": "\\cap", "∪": "\\cup",
    "∈": "\\in", "∉": "\\notin", "∋": "\\ni",
    "⊂": "\\subset", "⊃": "\\supset", "⊆": "\\subseteq", "⊇": "\\supseteq",
    "∀": "\\forall", "∃": "\\exists", "∄": "\\nexists",
    "∅": "\\emptyset", "∞": "\\infty", "∂": "\\partial", "∇": "\\nabla",
    "→": "\\to", "←": "\\leftarrow", "↔": "\\leftrightarrow",
    "⇒": "\\Rightarrow", "⇐": "\\Leftarrow", "⇔": "\\Leftrightarrow",
    "↦": "\\mapsto", "⟶": "\\longrightarrow", "⟵": "\\longleftarrow",
    "∠": "\\angle", "⊥": "\\perp", "∥": "\\parallel", "¬": "\\neg",
    "′": "'", "″": "''", "‴": "'''",
    "…": "\\dots", "⋯": "\\cdots", "⋮": "\\vdots", "⋱": "\\ddots",
    "√": "\\sqrt", "□": "\\square", "△": "\\triangle",
    "ℵ": "\\aleph", "ℏ": "\\hbar", "ℜ": "\\Re", "ℑ": "\\Im",
    "−": "-", "∕": "/",
}

#: 两侧不加空格的运算符（括号 / 分隔符）
_TIGHT_OPERATORS = frozenset("()[]{},;:|/")

#: ``<mi mathvariant>`` → LaTeX 字体命令
_MI_VARIANTS: dict[str, str] = {
    "double-struck": "mathbb", "script": "mathcal", "bold-script": "mathcalbold",
    "fraktur": "mathfrak", "bold-fraktur": "mathbffrak",
    "bold": "mathbf", "italic": "mathit", "bold-italic": "boldsymbol",
    "sans-serif": "mathsf", "bold-sans-serif": "mathboldsf",
    "monospace": "mathtt", "normal": "mathrm", "upright": "mathrm",
}

#: 大算符（``munderover`` 的上下限直接作为脚本，由排版层堆叠）
_BIGOP_COMMANDS = frozenset({
    "\\sum", "\\prod", "\\coprod", "\\int", "\\oint", "\\iint", "\\iiint",
    "\\bigcup", "\\bigcap", "\\bigvee", "\\bigwedge", "\\bigoplus",
    "\\bigotimes", "\\bigsqcup", "\\biguplus", "\\lim", "\\max", "\\min",
    "\\sup", "\\inf", "\\det", "\\limsup", "\\liminf",
})

#: 上/下加注字符 → 重音命令（``mover``/``munder`` 的单字符标注）
_ACCENT_COMMANDS: dict[str, str] = {
    "^": "hat", "\u0302": "hat", "\u203e": "overline", "\u00af": "bar",
    "\u2192": "vec", "\u20d7": "vec", "\u0307": "dot", "\u0308": "ddot",
    "\u02c7": "check", "\u02dc": "tilde", "\u0303": "tilde",
    "\u0304": "bar", "\u23de": "overbrace", "\u23b4": "overbracket",
    "\u23dc": "overparen",
}

#: 下标注字符 → 下加重音命令
_UNDER_ACCENT_COMMANDS: dict[str, str] = {
    "\u23df": "underbrace", "\u23b5": "underbracket", "\u23dd": "underparen",
}

#: 定界符 → LaTeX ``\left``/``\right`` 参数
_DELIM_LATEX: dict[str, str] = {
    "(": "(", ")": ")", "[": "[", "]": "]", "{": "\\{", "}": "\\}",
    "|": "|", "‖": "\\|", "⟨": "\\langle", "⟩": "\\rangle",
    "⌊": "\\lfloor", "⌋": "\\rfloor", "⌈": "\\lceil", "⌉": "\\rceil",
    "": ".", ".": ".",
}


def _delim(ch: str) -> str:
    return _DELIM_LATEX.get(ch or "", ch or ".")


# ── 节点 → LaTeX ──────────────────────────────────────────


def _child(node: _Node, idx: int, depth: int) -> str:
    if idx < len(node.children):
        return _to_latex(node.children[idx], depth + 1)
    return ""


def _sequence(node: _Node, depth: int) -> str:
    """节点内容（文本 + 子节点 + 尾随文本）顺序拼接。"""
    parts = [_escape_math_text(node.text)]
    for child in node.children:
        parts.append(_to_latex(child, depth + 1))
        parts.append(_escape_math_text(child.tail))
    return "".join(parts)


def _h_row(node: _Node, depth: int) -> str:
    return _sequence(node, depth)


def _h_mi(node: _Node, depth: int) -> str:
    text = _raw_text(node).strip()
    if not text:
        return ""
    variant = (node.attrs.get("mathvariant") or "").strip().lower()
    cmd = _MI_VARIANTS.get(variant)
    if cmd and len(text) > 0:
        return "\\" + cmd + "{" + _escape_math_text(text) + "}"
    return _escape_math_text(text)


def _h_mn(node: _Node, depth: int) -> str:
    return _escape_math_text(_raw_text(node).strip())


def _h_mo(node: _Node, depth: int) -> str:
    text = _raw_text(node).strip()
    if not text:
        return ""
    latex = _OPERATOR_LATEX.get(text, _escape_math_text(text))
    if text in _TIGHT_OPERATORS or len(text) == 1 and text in "()[]":
        return latex
    return " " + latex + " "


def _h_mtext(node: _Node, depth: int) -> str:
    return "\\text{" + _escape_text(_raw_text(node)) + "}"


def _h_ms(node: _Node, depth: int) -> str:
    text = _raw_text(node)
    lquote = node.attrs.get("lquote", '"')
    rquote = node.attrs.get("rquote", '"')
    return ("\\text{" + _escape_text(lquote + text + rquote) + "}")


def _h_mspace(node: _Node, depth: int) -> str:
    return " "


def _h_frac(node: _Node, depth: int) -> str:
    num = _child(node, 0, depth).strip()
    den = _child(node, 1, depth).strip()
    return "\\frac{" + num + "}{" + den + "}"


def _h_sqrt(node: _Node, depth: int) -> str:
    return "\\sqrt{" + _sequence(node, depth).strip() + "}"


def _h_root(node: _Node, depth: int) -> str:
    base = _child(node, 0, depth).strip()
    degree = _child(node, 1, depth).strip()
    if not degree:
        return "\\sqrt{" + base + "}"
    return "\\sqrt[" + degree + "]{" + base + "}"


def _h_sup(node: _Node, depth: int) -> str:
    return _child(node, 0, depth) + "^{" + _child(node, 1, depth).strip() + "}"


def _h_sub(node: _Node, depth: int) -> str:
    return _child(node, 0, depth) + "_{" + _child(node, 1, depth).strip() + "}"


def _h_subsup(node: _Node, depth: int) -> str:
    base = _child(node, 0, depth)
    return (base + "_{" + _child(node, 1, depth).strip() + "}"
            + "^{" + _child(node, 2, depth).strip() + "}")


def _h_under(node: _Node, depth: int) -> str:
    base = _child(node, 0, depth).strip()
    raw = _raw_text(node.children[1]).strip() if len(node.children) > 1 else ""
    label = _child(node, 1, depth).strip()
    cmd = _UNDER_ACCENT_COMMANDS.get(raw) or _UNDER_ACCENT_COMMANDS.get(label)
    if cmd:
        return "\\" + cmd + "{" + base + "}"
    return "\\underset{" + label + "}{" + base + "}"


def _h_over(node: _Node, depth: int) -> str:
    base = _child(node, 0, depth).strip()
    raw = _raw_text(node.children[1]).strip() if len(node.children) > 1 else ""
    label = _child(node, 1, depth).strip()
    cmd = _ACCENT_COMMANDS.get(raw) or _ACCENT_COMMANDS.get(label)
    if cmd:
        return "\\" + cmd + "{" + base + "}"
    return "\\overset{" + label + "}{" + base + "}"


def _h_underover(node: _Node, depth: int) -> str:
    base = _child(node, 0, depth).strip()
    sub = _child(node, 1, depth).strip()
    sup = _child(node, 2, depth).strip()
    # 大算符：上下限直接作为脚本（块级时排版层会上下堆叠），尾随空格与
    # 后续内容分隔（终端无 LaTeX 的自动数学间距）。
    if base in _BIGOP_COMMANDS:
        out = base
        if sub:
            out += "_{" + sub + "}"
        if sup:
            out += "^{" + sup + "}"
        return out + " "
    if sup:
        raw = _raw_text(node.children[2]).strip() if len(node.children) > 2 else ""
        cmd = _ACCENT_COMMANDS.get(raw) or _ACCENT_COMMANDS.get(sup)
        if cmd and not sub:
            return "\\" + cmd + "{" + base + "}"
    out = base
    if sub:
        out = "\\underset{" + sub + "}{" + out + "}"
    if sup:
        out = "\\overset{" + sup + "}{" + out + "}"
    return out


def _h_fenced(node: _Node, depth: int) -> str:
    open_ch = node.attrs.get("open", "(")
    close_ch = node.attrs.get("close", ")")
    seps = node.attrs.get("separators", ",")
    sep = seps[0] if seps else ","
    parts = [_to_latex(c, depth + 1).strip() for c in node.children]
    parts = [p for p in parts if p]
    # 子节点自带分隔符（``<mo>,</mo>``）时不再插入 separators，避免重复逗号
    if any(p == sep for p in parts):
        inner = " ".join(parts)
    else:
        inner = (" " + sep + " ").join(parts)
    return "\\left" + _delim(open_ch) + " " + inner + " \\right" + _delim(close_ch)


def _h_table(node: _Node, depth: int) -> str:
    rows: list[str] = []
    for row in node.children:
        if row.tag in ("mtr", "mlabeledtr", "mtd"):
            cells = [_to_latex(cell, depth + 1).strip()
                     for cell in row.children if cell.tag == "mtd"]
            if not cells and row.tag == "mtd":
                cells = [_to_latex(row, depth + 1).strip()]
            rows.append(" & ".join(cells))
    if not rows:
        return _sequence(node, depth)
    return "\\begin{matrix}" + " \\\\ ".join(rows) + "\\end{matrix}"


def _h_enclosure(node: _Node, depth: int) -> str:
    inner = _sequence(node, depth).strip()
    notation = (node.attrs.get("notation") or "").strip().lower()
    if any(k in notation for k in ("strike", "diagonalstrike")):
        return "\\cancel{" + inner + "}"
    if any(k in notation for k in ("box", "circle", "roundedbox")):
        return "\\boxed{" + inner + "}"
    return inner


def _h_phantom(node: _Node, depth: int) -> str:
    return "\\phantom{" + _sequence(node, depth).strip() + "}"


def _h_semantics(node: _Node, depth: int) -> str:
    for child in node.children:
        if child.tag not in ("annotation", "annotation-xml"):
            return _to_latex(child, depth + 1)
    return ""


def _h_multiscripts(node: _Node, depth: int) -> str:
    return _sequence(node, depth)


def _h_skip(node: _Node, depth: int) -> str:
    return ""


#: MathML 元素 → 转换函数（新增元素只登记本表）
_HANDLERS: dict[str, object] = {
    "math": _h_row,
    "mrow": _h_row,
    "mstyle": _h_row,
    "mpadded": _h_row,
    "merror": _h_row,
    "maction": _h_row,
    "semantics": _h_semantics,
    "annotation": _h_skip,
    "annotation-xml": _h_skip,
    "none": _h_skip,
    "mprescripts": _h_skip,
    "mi": _h_mi,
    "mn": _h_mn,
    "mo": _h_mo,
    "mtext": _h_mtext,
    "ms": _h_ms,
    "mspace": _h_mspace,
    "mfrac": _h_frac,
    "msqrt": _h_sqrt,
    "mroot": _h_root,
    "msup": _h_sup,
    "msub": _h_sub,
    "msubsup": _h_subsup,
    "munder": _h_under,
    "mover": _h_over,
    "munderover": _h_underover,
    "mfenced": _h_fenced,
    "mtable": _h_table,
    "mtr": _h_row,
    "mtd": _h_row,
    "mlabeledtr": _h_row,
    "menclose": _h_enclosure,
    "mphantom": _h_phantom,
    "mmultiscripts": _h_multiscripts,
}


def _to_latex(node: _Node, depth: int) -> str:
    if depth > _MAX_DEPTH:
        return _escape_math_text(_raw_text(node))
    handler = _HANDLERS.get(node.tag)
    if handler is None:
        return _sequence(node, depth)
    return handler(node, depth)


def mathml_to_latex(source: str) -> str | None:
    """MathML 源码 → LaTeX 源码（无有效内容/解析失败返回 ``None``）。"""
    node = parse_mathml(source)
    if node is None:
        return None
    try:
        latex = _to_latex(node, 0).strip()
    except Exception:
        return None
    return latex or None


def render_mathml(source: str, inline: bool = False):
    """MathML 源码 → ``_Box``（解析失败返回 ``None``，调用方回退原文）。"""
    latex = mathml_to_latex(source)
    if not latex:
        return None
    try:
        return render_math_box(latex, inline=inline)
    except Exception:
        return None


def register_mathml_element(tag: str, handler) -> None:
    """注册 MathML 元素转换函数（插件扩展点：``handler(node, depth) -> str``）。"""
    if tag and callable(handler):
        _HANDLERS[tag.lower()] = handler


__all__ = [
    "parse_mathml", "mathml_to_latex", "render_mathml",
    "register_mathml_element",
]
