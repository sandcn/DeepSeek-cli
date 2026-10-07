"""_math_macros — 宏定义与展开（KaTeX 兼容的轻量 TeX 宏系统，零 Rich）。

TUI 流式公式渲染在遇到 ``\\def`` / ``\\newcommand`` / ``\\DeclareMathOperator``
/ ``\\let`` 等定义命令时，需要把定义登记下来并在后续（含跨公式的 ``\\gdef``）
展开，而不是把命令原文渲染出来。本模块实现：

  - 定义解析：``\\def`` / ``\\gdef`` / ``\\edef`` / ``\\xdef`` /
    ``\\newcommand`` / ``\\renewcommand`` / ``\\providecommand`` / ``\\let`` /
    ``\\DeclareMathOperator`` / ``\\newenvironment``（含 ``\\global`` 前缀）；
  - 宏展开：参数（``#1``…``#9``）替换、可选参数默认值、递归展开（带深度上限）；
  - 条件与工具命令：``\\if`` / ``\\ifx`` / ``\\ifmode`` / ``\\else`` / ``\\fi``
    （保留真分支）、``\\mathchoice`` / ``\\TextOrMath`` / ``\\@firstoftwo`` /
    ``\\@secondoftwo`` / ``\\@ifstar`` / ``\\@ifnextchar``；
  - 内置宏：``\\bra`` / ``\\ket`` / ``\\braket`` / ``\\Bra`` / ``\\Ket`` /
    ``\\Braket`` / ``\\set`` / ``\\Set`` / ``\\TeX`` / ``\\LaTeX`` / ``\\KaTeX``
    等（与 KaTeX 语义一致）。

扩展方式：新增内置宏只改 ``_BUILTIN_MACROS``；新增定义命令只改
``_DEF_COMMANDS`` 与 ``_parse_definition``。
"""

from __future__ import annotations

import re

#: 递归展开深度上限（防 ``\def\a{\a}`` 之类自引用导致栈溢出）
_MAX_EXPAND_DEPTH = 32

#: 参数占位符匹配（``#1`` … ``#9``）
_PARAM_RE = re.compile(r"#([1-9])")

#: 定义类命令（值 = 该命令的解析模式）
_DEF_COMMANDS: dict[str, str] = {
    "def": "def", "gdef": "gdef", "edef": "edef", "xdef": "xdef",
    "newcommand": "newcommand", "renewcommand": "renewcommand",
    "providecommand": "providecommand",
    "DeclareMathOperator": "operatorname",
    "newenvironment": "newenvironment",
    "renewenvironment": "newenvironment",
    "Newextarrow": "newextarrow",
    "let": "let",
}

#: 仅消耗参数、无渲染效果的命令（预处理阶段移除；值 = 参数个数）
_IGNORED_ARG_COMMANDS: dict[str, int] = {
    "setlength": 2, "skip": 1, "toggle": 1, "require": 1,
    "fcopy": 1, "pagecolor": 1, "mathclose": 0, "mathopen": 0,
    "mathpunct": 0, "begingroup": 0, "endgroup": 0, "relax": 0,
    "long": 0, "futurelet": 0, "noexpand": 0, "expandafter": 0,
    "vspace": 1, "root": 1,
    "oldstyle": 0, "upshape": 0, "itshape": 0, "bfseries": 0,
    "mdseries": 0, "rmfamily": 0, "sffamily": 0, "ttfamily": 0,
    # 字号声明（终端无字号概念）
    "tiny": 0, "Tiny": 0, "scriptsize": 0, "footnotesize": 0,
    "small": 0, "normalsize": 0, "large": 0, "Large": 0,
    "LARGE": 0, "huge": 0, "Huge": 0, "sixptsize": 0,
    # 字体切换声明（不接收参数）
    "sl": 0, "sc": 0, "tt": 0, "sf": 0, "rm": 0, "bf": 0, "it": 0,
    "mit": 0, "scr": 0, "cal": 0, "em": 0, "md": 0, "up": 0,
}

#: 水平间距命令（消耗 1 个参数后输出一个空格）
_SPACE_ARG_COMMANDS: dict[str, int] = {
    "hskip": 1, "mspace": 1, "hspace": 1,
    "kern": 1, "mkern": 1, "mskip": 1,
}


class Macro:
    """一个宏定义（名字由调用方保存）。"""

    __slots__ = ("body", "nargs", "optional_default")

    def __init__(self, body: str, nargs: int = 0,
                 optional_default: str | None = None) -> None:
        self.body = body
        self.nargs = nargs
        self.optional_default = optional_default

    def expand(self, args: list[str]) -> str:
        """用实参替换 ``#n`` 占位符（缺参补空）。"""
        def _sub(m: "re.Match[str]") -> str:
            idx = int(m.group(1))
            if 1 <= idx <= len(args):
                return args[idx - 1]
            return ""
        return _PARAM_RE.sub(_sub, self.body)


class MacroState:
    """跨公式共享的宏系统状态（全局宏表 + 版本号 + 自定义环境表）。"""

    def __init__(self) -> None:
        self.shared: dict[str, Macro] = {}
        self.custom_envs: dict[str, tuple[int, str, str]] = {}
        #: 版本号：任何全局定义（``\gdef`` / ``\newcommand``…）递增——
        #: 渲染缓存以此判失效（宏变化会改变展开结果）。
        self.version = 0


class MacroTable:
    """宏表：局部（公式内）与全局（``\\gdef`` 持久到后续公式）。"""

    def __init__(self, state: "MacroState | None" = None) -> None:
        self.state = state if state is not None else MacroState()
        self.local: dict[str, Macro] = {}

    @property
    def shared(self) -> dict[str, "Macro"]:
        return self.state.shared

    @property
    def custom_envs(self) -> dict[str, tuple[int, str, str]]:
        return self.state.custom_envs

    def get(self, name: str) -> "Macro | None":
        return self.local.get(name) or self.state.shared.get(name)

    def define(self, name: str, macro: "Macro", global_scope: bool = False,
               force: bool = True) -> None:
        if not force and self.get(name) is not None:
            return
        if global_scope:
            self.state.shared[name] = macro
            self.state.version += 1
        else:
            self.local[name] = macro


#: KaTeX 内置宏（值 = 展开体）；带参数者见 ``_BUILTIN_NARGS``
_BUILTIN_MACROS: dict[str, str] = {
    # ── bra-ket 记号 ──
    "bra": r"\left\langle #1\right|",
    "ket": r"\left|#1\right\rangle",
    "braket": r"\left\langle #1\right\rangle",
    "Bra": r"\left\langle #1\right|",
    "Ket": r"\left|#1\right\rangle",
    "Braket": r"\left\langle #1\right\rangle",
    # ── 集合 ──
    "set": r"\left\{#1\right\}",
    "Set": r"\left\{#1\right\}",
    # ── 排版 logo（按原文呈现）──
    "TeX": r"\text{TeX}",
    "LaTeX": r"\text{LaTeX}",
    "KaTeX": r"\text{KaTeX}",
    # ── 角度记号 ──
    "ang": r"\text{°}",
    "angl": r"\text{°}",
    "angln": r"\text{°}",
    # ── 化学式（mhchem 简化：按原文呈现内容）──
    "ce": r"\text{#1}", "cee": r"\text{#1}",
    "cf": r"\text{#1}", "pu": r"\text{#1}",
    # ── 文本模式标点与空格 ──
    "lq": "\u2018", "rq": "\u2019",
    "space": " ", "nobreakspace": " ",
}

#: 内置宏的参数个数（名字 → 参数数）
_BUILTIN_NARGS: dict[str, int] = {
    "bra": 1, "ket": 1, "braket": 1, "Bra": 1, "Ket": 1, "Braket": 1,
    "set": 1, "Set": 1, "ce": 1, "cee": 1, "cf": 1, "pu": 1,
}


def _read_arg(s: str, i: int) -> tuple[str, int]:
    """读取一个宏实参（``{...}`` 组原文或单个 token）→ ``(原文, 新位置)``。"""
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
    if s[i] == "\\":
        j = i + 1
        if j < n and not (s[j].isalpha() or s[j] == "@"):
            return s[i:j + 1], j + 1
        while j < n and (s[j].isalpha() or s[j] == "@"):
            j += 1
        return s[i:j], j
    return s[i], i + 1


def _read_braced(s: str, i: int) -> tuple[str, int]:
    """读取 ``{...}``（不存在时返回 ``("", i)``）。"""
    n = len(s)
    while i < n and s[i] == " ":
        i += 1
    if i >= n or s[i] != "{":
        return "", i
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


def _read_cmd_name(s: str, i: int) -> tuple[str, int]:
    """读取 ``\\name``（``i`` 指向反斜杠）→ ``(名字, 新位置)``。"""
    n = len(s)
    if i >= n or s[i] != "\\":
        return "", i
    j = i + 1
    while j < n and (s[j].isalpha() or s[j] == "@"):
        j += 1
    return s[i + 1:j], j


def _builtin_table(state: MacroState) -> MacroTable:
    table = MacroTable(state)
    for name, body in _BUILTIN_MACROS.items():
        table.local[name] = Macro(body, _BUILTIN_NARGS.get(name, 0))
    return table


def _resolve_conditionals(s: str) -> str:
    """处理 ``\\if…\\else…\\fi``：保留真分支（``\\ifx`` 作字面相等比较）。

    其余 ``\\if`` 系列在终端渲染中按「真」处理——保证命令文本不残留、
    内容不丢失。
    """
    out: list[str] = []
    i = 0
    n = len(s)
    stack: list[dict] = []
    while i < n:
        if s[i] == "\\":
            name, j = _read_cmd_name(s, i)
            if name in ("if", "ifx", "ifmode", "ifnum", "ifdim", "iftrue",
                        "iffalse", "ifmmode", "ifcat", "ifodd", "ifvmode",
                        "ifhmode", "ifinner"):
                if name == "ifx":
                    a, k = _read_arg(s, j)
                    b, k = _read_arg(s, k)
                    cond = a == b
                    j = k
                elif name == "iffalse":
                    cond = False
                else:
                    cond = True
                stack.append({"cond": cond, "else": False})
                i = j
                continue
            if name == "else":
                if stack:
                    stack[-1]["else"] = True
                i = j
                continue
            if name == "fi":
                if stack:
                    stack.pop()
                i = j
                continue
            if j > i:
                out.append(s[i:j])
                i = j
                continue
        if stack and not (stack[-1]["cond"] and not stack[-1]["else"]):
            i += 1
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _strip_ignored(s: str) -> str:
    """移除无渲染效果、仅消耗参数的命令（``\\setlength`` / 字号声明等）。

    ``\\hspace`` / ``\\kern`` 等水平间距命令替换为空格（终端无精确宽度）。
    """
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n and (s[i + 1].isalpha() or s[i + 1] == "@"):
            name, j = _read_cmd_name(s, i)
            nargs = _IGNORED_ARG_COMMANDS.get(name, _SPACE_ARG_COMMANDS.get(name))
            if nargs is not None:
                k = j
                while k < n and s[k] == "*":
                    k += 1
                for _ in range(nargs):
                    _, k = _read_arg(s, k)
                if name in _SPACE_ARG_COMMANDS:
                    out.append(" ")
                i = k
                continue
            out.append(s[i:j])
            i = j
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _read_optional_nargs(s: str, i: int) -> tuple[int, int]:
    """读取 ``[n]`` 参数个数（不存在时返回 0）。"""
    while i < len(s) and s[i] == " ":
        i += 1
    if i < len(s) and s[i] == "[":
        end = s.find("]", i)
        if end > 0:
            spec = s[i + 1:end].strip()
            try:
                return (int(spec) if spec else 0), end + 1
            except ValueError:
                return 0, end + 1
    return 0, i


def _parse_definition(s: str, i: int, cmd: str, table: MacroTable,
                      global_prefix: bool = False) -> int:
    """解析一条定义命令并登记宏，返回其后的源码位置。"""
    global_scope = global_prefix or cmd in ("gdef", "xdef")
    if cmd == "operatorname":
        if i < len(s) and s[i] == "*":
            i += 1
            op = "\\operatorname*"
        else:
            op = "\\operatorname"
        name_src, i = _read_arg(s, i)
        name, _ = _read_cmd_name(name_src, 0)
        body, i = _read_braced(s, i)
        if name:
            table.define(name, Macro(op + "{" + body + "}"),
                         global_scope=True)
        return i
    if cmd == "newenvironment":
        name, i = _read_arg(s, i)
        nargs, i = _read_optional_nargs(s, i)
        begin_body, i = _read_braced(s, i)
        end_body, i = _read_braced(s, i)
        if name:
            table.state.custom_envs[name] = (nargs, begin_body, end_body)
            table.state.version += 1
        return i
    if cmd == "newextarrow":
        # ``\Newextarrow{\name}{num}{codes}``：登记为扩展箭头宏
        name_src, i = _read_arg(s, i)
        name, _ = _read_cmd_name(name_src, 0)
        self_i = i
        for _ in range(2):
            _, self_i = _read_arg(s, self_i)
        i = self_i
        if name:
            table.define(name, Macro("\\xrightarrow"), global_scope=True)
        return i
    if cmd in ("newcommand", "renewcommand", "providecommand"):
        while i < len(s) and s[i] == " ":
            i += 1
        if i < len(s) and s[i] == "{":
            inner, i = _read_arg(s, i)
            name, _ = _read_cmd_name(inner, 0)
        else:
            name, i = _read_cmd_name(s, i)
        nargs, i = _read_optional_nargs(s, i)
        optional_default = None
        if i < len(s) and s[i] == "[":
            end = s.find("]", i)
            if end > 0:
                optional_default = s[i + 1:end]
                i = end + 1
        body, i = _read_braced(s, i)
        if name:
            table.define(name, Macro(body, nargs, optional_default),
                         global_scope=True,
                         force=(cmd != "providecommand"))
        return i
    if cmd == "let":
        target, i = _read_cmd_name(s, i)
        while i < len(s) and s[i] in " =":
            i += 1
        source, i = _read_arg(s, i)
        if target:
            table.define(target, Macro(source), global_scope=global_scope)
        return i
    # ``\def`` / ``\gdef`` / ``\edef`` / ``\xdef``
    while i < len(s) and s[i] == " ":
        i += 1
    if i < len(s) and s[i] == "{":
        inner, i = _read_arg(s, i)
        name, _ = _read_cmd_name(inner, 0)
    else:
        name, i = _read_cmd_name(s, i)
    nargs = 0
    while i < len(s) and s[i] == "#" and i + 1 < len(s) and s[i + 1].isdigit():
        nargs = max(nargs, int(s[i + 1]))
        i += 2
    body, i = _read_braced(s, i)
    if name:
        table.define(name, Macro(body, nargs), global_scope=global_scope)
    return i


def _strip_definitions(s: str, table: MacroTable) -> str:
    """扫描源码：登记定义命令（``\\def`` 等）并从源码中移除。"""
    out: list[str] = []
    i = 0
    n = len(s)
    global_prefix = False
    while i < n:
        if s[i] == "\\" and i + 1 < n and (s[i + 1].isalpha() or s[i + 1] == "@"):
            name, j = _read_cmd_name(s, i)
            if name in ("global", "long"):
                global_prefix = global_prefix or name == "global"
                i = j
                continue
            if name in _DEF_COMMANDS:
                i = _parse_definition(s, j, name, table, global_prefix)
                global_prefix = False
                continue
            global_prefix = False
        out.append(s[i])
        i += 1
    return "".join(out)


def _expand(s: str, table: MacroTable, depth: int = 0) -> str:
    """展开宏调用（参数替换 + 递归；``depth`` 防自引用无限递归）。"""
    if depth > _MAX_EXPAND_DEPTH or "\\" not in s:
        return s
    out: list[str] = []
    i = 0
    n = len(s)
    changed = False
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n and (s[i + 1].isalpha() or s[i + 1] == "@"):
            name, j = _read_cmd_name(s, i)
            macro = table.get(name)
            if macro is not None:
                args: list[str] = []
                k = j
                if macro.optional_default is not None:
                    m = k
                    while m < n and s[m] == " ":
                        m += 1
                    if m < n and s[m] == "[":
                        end = s.find("]", m)
                        if end > 0:
                            args.append(s[m + 1:end])
                            k = end + 1
                        else:
                            args.append(macro.optional_default)
                    else:
                        args.append(macro.optional_default)
                for _ in range(max(0, macro.nargs - len(args))):
                    arg, k = _read_arg(s, k)
                    args.append(arg)
                out.append(macro.expand(args))
                i = k
                changed = True
                continue
            out.append(s[i:j])
            i = j
            continue
        if ch == "\\" and i + 1 < n:
            out.append(s[i:i + 2])
            i += 2
            continue
        out.append(ch)
        i += 1
    if not changed:
        return "".join(out)
    return _expand("".join(out), table, depth + 1)


def _resolve_utility_cmds(s: str) -> str:
    """处理工具命令：``\\mathchoice`` / ``\\TextOrMath`` / ``\\@firstoftwo`` 等。"""
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == "\\" and i + 1 < n and (s[i + 1].isalpha() or s[i + 1] == "@"):
            name, j = _read_cmd_name(s, i)
            if name == "mathchoice":
                first = ""
                k = j
                for idx in range(4):
                    arg, k = _read_arg(s, k)
                    if idx == 0:
                        first = arg
                out.append(first)
                i = k
                continue
            if name == "TextOrMath":
                _text, k = _read_arg(s, j)
                math, k = _read_arg(s, k)
                out.append(math)
                i = k
                continue
            if name in ("@firstoftwo", "@secondoftwo"):
                a, k = _read_arg(s, j)
                b, k = _read_arg(s, k)
                out.append(a if name == "@firstoftwo" else b)
                i = k
                continue
            if name == "@ifstar":
                star_branch, k = _read_arg(s, j)
                plain_branch, k = _read_arg(s, k)
                m = k
                while m < n and s[m] == " ":
                    m += 1
                if m < n and s[m] == "*":
                    out.append(star_branch)
                    i = m + 1
                else:
                    out.append(plain_branch)
                    i = k
                continue
            if name == "@ifnextchar":
                _ch, k = _read_arg(s, j)
                a, k = _read_arg(s, k)
                b, k = _read_arg(s, k)
                _ = b
                out.append(a)
                i = k
                continue
            out.append(s[i:j])
            i = j
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def preprocess_math_source(src: str, state: MacroState | None = None
                           ) -> tuple[str, MacroTable]:
    """公式源码预处理：提取定义 + 展开宏 + 解析条件与工具命令。

    Args:
        src: 公式 LaTeX 源码。
        state: 跨公式共享状态（``\\gdef`` 写入其中；``None`` 时新建）。

    Returns:
        ``(展开后的源码, 宏表)``。
    """
    table = _builtin_table(state if state is not None else MacroState())
    if not src:
        return "", table
    text = _resolve_conditionals(src)
    text = _strip_definitions(text, table)
    text = _strip_ignored(text)
    text = _resolve_utility_cmds(text)
    text = _expand(text, table)
    text = _resolve_utility_cmds(text)
    text = _expand(text, table)
    return text, table


class _MathMacroMixin:
    """宏系统 mixin：由 ``_LatexRenderer`` 继承（提供预处理入口）。"""

    def preprocess(self, src: str) -> str:
        text, self.macro_table = preprocess_math_source(src, self.macro_state)
        return text


__all__ = [
    "Macro", "MacroState", "MacroTable", "preprocess_math_source",
    "_MathMacroMixin", "_BUILTIN_MACROS",
]
