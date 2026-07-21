"""_block_helpers — 块级解析辅助函数与常量（无正则）。

从 recursive_parser.py 拆分而来，包含字符级块级语法检测函数。
"""

from __future__ import annotations

from ._utils import (
    _COMMON_LANGUAGES, _get_fence_info,
)


# ═══════════════════════════════════════════════════════════
# 字符级辅助函数（无正则）
# ═══════════════════════════════════════════════════════════

def _is_empty_line(line: str) -> bool:
    """判断是否为空白行（无正则）。"""
    if not line:
        return True
    first = line[0]
    if first not in ' \t\r\n':
        return False
    for ch in line:
        if ch not in ' \t\r\n':
            return False
    return True


def _strip_left(line: str) -> str:
    """去掉前导空白（无正则）。"""
    i = 0
    while i < len(line) and line[i] in ' \t':
        i += 1
    return line[i:]


def _rstrip_line(line: str) -> str:
    """去掉末尾空白和换行（无正则）。"""
    i = len(line) - 1
    while i >= 0 and line[i] in ' \t\r\n':
        i -= 1
    return line[:i + 1]


def _is_only_chars(line: str, ch: str) -> bool:
    """判断是否仅由 ch 字符和空白组成。"""
    for c in line:
        if c not in (' ', '\t', ch):
            return False
    return True


def _has_only_chars(s: str, chars: str) -> bool:
    """字符串是否仅包含指定字符集中的字符。"""
    for c in s:
        if c not in chars:
            return False
    return True


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

# 语言名称黑名单（防止意外识别）
_LANG_BLACKLIST: frozenset[str] = frozenset({
    'main', 'import', 'class', 'def', 'function', 'func', 'var', 'let',
    'const', 'return', 'if', 'else', 'for', 'while', 'do', 'switch',
    'case', 'try', 'catch', 'public', 'private', 'static', 'void',
    'int', 'float', 'double', 'char', 'bool', 'string', 'true', 'false',
    'null', 'nil', 'none', 'this', 'self', 'super', 'extends',
    'print', 'printf', 'println', 'todo', 'fixme',
})

# 块级 HTML 标签
_BLOCK_HTML_TAGS: frozenset[str] = frozenset({
    'div', 'pre', 'table', 'section', 'article', 'header', 'footer',
    'main', 'aside', 'nav', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li', 'dl', 'dt', 'dd', 'form', 'fieldset',
    'figure', 'figcaption', 'blockquote',
    'details', 'summary', 'dialog',
    'template', 'canvas', 'noscript', 'script', 'style', 'address',
    'center', 'hr', 'video', 'audio', 'picture', 'iframe',
    'progress', 'meter', 'menu',
})

# HTML 自闭合空标签（void elements）
_VOID_HTML_TAGS: frozenset[str] = frozenset({
    'br', 'hr', 'img', 'input', 'meta', 'link', 'area', 'base',
    'col', 'embed', 'param', 'source', 'track', 'wbr',
})

# 缩进代码块常量
_INDENT_SPACES = 4
_INDENT_PREFIX = ' ' * _INDENT_SPACES

# 安全性常量
_INLINE_MAX_DEPTH = 20
_MAX_PRESCAN_BUFFER = 200_000


# ═══════════════════════════════════════════════════════════
# 块级语法检测函数
# ═══════════════════════════════════════════════════════════

def _is_blockquote_line(stripped: str) -> bool:
    """判断行是否为引用块行（以 > 开头）。"""
    s = _strip_left(stripped)
    return len(s) > 0 and s[0] == '>'


def _get_blockquote_text(stripped: str) -> str:
    """提取引用块中 > 后的内容。"""
    s = _strip_left(stripped)
    if s.startswith('> '):
        return s[2:]
    elif s.startswith('>'):
        return s[1:]
    return s


def _is_code_fence_line(stripped: str) -> bool:
    """判断行是否为代码 fence 行（``` 或 ~~~ 开头）。"""
    if not stripped:
        return False
    first = stripped[0]
    if first not in ('`', '~'):
        return False
    count = 0
    for ch in stripped:
        if ch == first:
            count += 1
        else:
            break
    return count >= 3


def _strip_blockquote_prefix(stripped: str) -> str:
    """剥离引用块前缀 >，返回内层内容（无正则）。"""
    s = _strip_left(stripped)
    if s.startswith('> '):
        return s[2:]
    if s.startswith('>'):
        return s[1:]
    return stripped


def _get_fence_lang(s: str) -> str:
    """从 fence 行剩余部分提取语言名。"""
    s = s.strip()
    if not s:
        return ''
    i = 0
    while i < len(s) and (s[i].isalnum() or s[i] in '+.#_-'):
        i += 1
    return s[:i].lower()


def _rstrip_trailing_hashes(text: str) -> str:
    """去掉标题末尾的 # 序列。"""
    result = text.rstrip()
    while result and result[-1] == '#':
        result = result[:-1].rstrip()
    return result
