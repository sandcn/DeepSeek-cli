"""_syntax — CodeBlock 轻量语法高亮（多语言，无第三方依赖）。

为 ``CodeBlock`` 控件提供内置高亮：按语言的关键字/字符串/注释/数字切分并
着色（256 色号），输出 ``StyledRun`` 列表序列（每行一段）。

设计取舍：本项目聊天渲染的 Markdown 代码块已由 Pygments 高亮；本模块只服务
TUI 控件库内的 ``CodeBlock``（无 Pygments 依赖、纯内置、可预测），并覆盖
更多语言的关键字表。
"""

from __future__ import annotations

from typing import Iterable

from src.tui.core.style import Style
from src.tui.ink.output import StyledRun

__all__ = [
    "SUPPORTED_LANGUAGES",
    "highlight_lines",
    "tokenize_line",
    "normalize_language",
    "is_supported",
    "plain_runs",
]

#: 颜色槽（256 色号）
_C_KEYWORD = Style(fg=175, bold=True)
_C_STRING = Style(fg=114)
_C_COMMENT = Style(fg=242, italic=True)
_C_NUMBER = Style(fg=180)
_C_PLAIN = None

#: 语言别名归一化
_ALIASES = {
    "py": "python", "python3": "python",
    "js": "javascript", "jsx": "javascript", "node": "javascript",
    "ts": "typescript", "tsx": "typescript",
    "sh": "shell", "bash": "shell", "zsh": "shell", "console": "shell",
    "yml": "yaml", "c++": "cpp", "cs": "csharp", "rs": "rust",
    "golang": "go", "htm": "html",
}

#: 语言关键字表（覆盖常见语言；未知语言回退通用高亮）。
_KEYWORDS: dict[str, set[str]] = {
    "python": {
        "and", "as", "assert", "async", "await", "break", "class", "continue",
        "def", "del", "elif", "else", "except", "finally", "for", "from",
        "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
        "or", "pass", "raise", "return", "try", "while", "with", "yield",
        "True", "False", "None", "self", "cls",
    },
    "javascript": {
        "var", "let", "const", "function", "return", "if", "else", "for",
        "while", "do", "switch", "case", "break", "continue", "new", "this",
        "class", "extends", "super", "import", "export", "default", "from",
        "async", "await", "try", "catch", "finally", "throw", "typeof",
        "instanceof", "delete", "in", "of", "null", "undefined", "true",
        "false", "yield", "static", "get", "set",
    },
    "typescript": {
        "var", "let", "const", "function", "return", "if", "else", "for",
        "while", "switch", "case", "break", "continue", "new", "this",
        "class", "extends", "implements", "interface", "type", "enum",
        "import", "export", "default", "from", "async", "await", "try",
        "catch", "finally", "throw", "typeof", "instanceof", "in", "of",
        "null", "undefined", "true", "false", "public", "private",
        "protected", "readonly", "namespace", "declare", "as",
    },
    "go": {
        "break", "case", "chan", "const", "continue", "default", "defer",
        "else", "fallthrough", "for", "func", "go", "goto", "if", "import",
        "interface", "map", "package", "range", "return", "select", "struct",
        "switch", "type", "var", "nil", "true", "false", "defer",
    },
    "rust": {
        "as", "async", "await", "break", "const", "continue", "crate", "dyn",
        "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in",
        "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return",
        "self", "struct", "super", "trait", "true", "type", "unsafe", "use",
        "where", "while", "Some", "None", "Ok", "Err",
    },
    "java": {
        "abstract", "assert", "boolean", "break", "byte", "case", "catch",
        "char", "class", "const", "continue", "default", "do", "double",
        "else", "enum", "extends", "final", "finally", "float", "for", "if",
        "implements", "import", "instanceof", "int", "interface", "long",
        "native", "new", "package", "private", "protected", "public",
        "return", "short", "static", "super", "switch", "synchronized",
        "this", "throw", "throws", "transient", "try", "void", "volatile",
        "while", "true", "false", "null",
    },
    "c": {
        "auto", "break", "case", "char", "const", "continue", "default",
        "do", "double", "else", "enum", "extern", "float", "for", "goto",
        "if", "int", "long", "register", "return", "short", "signed",
        "sizeof", "static", "struct", "switch", "typedef", "union",
        "unsigned", "void", "volatile", "while", "NULL",
    },
    "cpp": {
        "auto", "break", "case", "catch", "class", "const", "constexpr",
        "continue", "default", "delete", "do", "double", "else", "enum",
        "explicit", "extern", "false", "float", "for", "friend", "if",
        "inline", "int", "long", "namespace", "new", "nullptr", "operator",
        "override", "private", "protected", "public", "return", "short",
        "signed", "sizeof", "static", "struct", "switch", "template",
        "this", "throw", "true", "try", "typedef", "typename", "union",
        "unsigned", "using", "virtual", "void", "volatile", "while",
    },
    "ruby": {
        "alias", "and", "begin", "break", "case", "class", "def", "do",
        "else", "elsif", "end", "ensure", "false", "for", "if", "in", "module",
        "next", "nil", "not", "or", "redo", "rescue", "retry", "return",
        "self", "super", "then", "true", "undef", "unless", "until", "when",
        "while", "yield", "require",
    },
    "shell": {
        "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
        "case", "esac", "function", "return", "export", "local", "echo",
        "cd", "set", "unset", "source", "in", "select",
    },
    "sql": {
        "select", "from", "where", "insert", "into", "values", "update",
        "set", "delete", "create", "table", "drop", "alter", "join", "left",
        "right", "inner", "outer", "on", "group", "by", "order", "having",
        "limit", "offset", "union", "all", "distinct", "as", "and", "or",
        "not", "null", "primary", "key", "foreign", "references", "index",
    },
    "yaml": {"true", "false", "null", "yes", "no", "on", "off"},
    "json": {"true", "false", "null"},
    "css": {"important", "media", "import", "keyframes", "from", "to"},
    "html": set(),
}

#: 行注释前缀（按语言；``#`` 通用回退）
_LINE_COMMENT = {
    "python": "#", "shell": "#", "yaml": "#", "ruby": "#",
    "javascript": "//", "typescript": "//", "go": "//", "rust": "//",
    "java": "//", "c": "//", "cpp": "//", "css": None, "sql": "--",
    "json": None, "html": None,
}

#: 字符串引号
_QUOTES = "'\"`"

_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_BODY = _IDENT_START | set("0123456789")
_DIGITS = set("0123456789")


SUPPORTED_LANGUAGES = tuple(sorted(_KEYWORDS))


def normalize_language(language) -> str:
    """语言名归一化（别名 → 规范名；未知 → 小写原名）。"""
    if not language:
        return ""
    name = str(language).strip().lower()
    return _ALIASES.get(name, name)


def _tokenize_line(line: str, keywords: set, comment: str | None) -> list[StyledRun]:
    """把单行代码切分为 StyledRun 序列（字符串/注释/数字/关键字/普通）。"""
    runs: list[StyledRun] = []
    buf: list[str] = []

    def _flush():
        if buf:
            runs.append(StyledRun("".join(buf), _C_PLAIN))
            buf.clear()

    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        # 行注释
        if comment and line.startswith(comment, i):
            _flush()
            runs.append(StyledRun(line[i:], _C_COMMENT))
            return runs
        # 字符串
        if ch in _QUOTES:
            _flush()
            j = i + 1
            while j < n:
                if line[j] == "\\":
                    j += 2
                    continue
                if line[j] == ch:
                    j += 1
                    break
                j += 1
            runs.append(StyledRun(line[i:j], _C_STRING))
            i = j
            continue
        # 数字
        if ch in _DIGITS:
            _flush()
            j = i
            while j < n and (line[j] in _DIGITS or line[j] == "."):
                j += 1
            runs.append(StyledRun(line[i:j], _C_NUMBER))
            i = j
            continue
        # 标识符/关键字
        if ch in _IDENT_START:
            j = i
            while j < n and line[j] in _IDENT_BODY:
                j += 1
            word = line[i:j]
            if word in keywords:
                _flush()
                runs.append(StyledRun(word, _C_KEYWORD))
            else:
                buf.append(word)
            i = j
            continue
        # 普通字符
        buf.append(ch)
        i += 1
    _flush()
    return runs or [StyledRun("", None)]


def highlight_lines(code: str, language: str) -> list[list[StyledRun]]:
    """按语言高亮整段代码，返回每行的 StyledRun 列表。"""
    lang = normalize_language(language)
    keywords = _KEYWORDS.get(lang, set())
    comment = _LINE_COMMENT.get(lang)
    lines = code.split("\n") if code else [""]
    return [_tokenize_line(line, keywords, comment) for line in lines]


def tokenize_line(line: str, language: str) -> list[StyledRun]:
    """按语言高亮单行（CodeBlock 逐行渲染使用）。"""
    lang = normalize_language(language)
    keywords = _KEYWORDS.get(lang, set())
    comment = _LINE_COMMENT.get(lang)
    return _tokenize_line(line, keywords, comment)


def is_supported(language: str) -> bool:
    """是否内置了该语言的关键字表。"""
    return normalize_language(language) in _KEYWORDS


def plain_runs(lines: Iterable[str]) -> list[list[StyledRun]]:
    """无高亮模式：逐行返回单 run。"""
    return [[StyledRun(str(line), None)] for line in lines]
