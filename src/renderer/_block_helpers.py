"""_block_helpers — 块级解析辅助函数与常量（无正则）。

从 recursive_parser.py 拆分而来，包含字符级块级语法检测函数。
"""

from __future__ import annotations

# Front Matter（文档头元信息块）判定逻辑集中在 ``_front_matter``（两渲染路径
# 共享的纯逻辑模块），此处 re-export 供块级解析器使用。
from ._front_matter import (  # noqa: E402
    front_matter_delim as _front_matter_delim,
    front_matter_format as _front_matter_format,
    is_front_matter_close as _is_front_matter_close,
)

__all__ = [
    "_is_empty_line",
    "_strip_left",
    "_rstrip_line",
    "_is_only_chars",
    "_has_only_chars",
    "_LANG_BLACKLIST",
    "_BLOCK_HTML_TAGS",
    "_VOID_HTML_TAGS",
    "_INDENT_SPACES",
    "_INDENT_PREFIX",
    "_INLINE_MAX_DEPTH",
    "_MAX_PRESCAN_BUFFER",
    "_is_blockquote_line",
    "_get_blockquote_text",
    "_split_blockquote",
    "_is_code_fence_line",
    "_strip_blockquote_prefix",
    "_get_fence_lang",
    "_rstrip_trailing_hashes",
    "has_block_markers",
    # 兼容面：_front_matter 纯逻辑的 re-export（块级解析器经由本模块导入）
    "_front_matter_delim",
    "_front_matter_format",
    "_is_front_matter_close",
]


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
    'progress', 'meter', 'menu', 'svg', 'math',
    # ★ 第二批扩展：控件与嵌入对象（单独成行时按其语义渲染，
    #   而非作为普通段落文本）
    'button', 'select', 'textarea', 'object',
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


def _split_blockquote(stripped: str) -> tuple[int, str]:
    """拆解引用块行 → ``(嵌套深度, 内层文本)``（无正则）。

    与 ``RegexFreeBlockParser._parse_blockquote`` 的深度计数/文本提取逻辑
    保持一致（``>`` 连续序列计数、剥离前缀），供流式预览做无副作用的行分类。

    ★ 性能（流式预览热路径）：只扫描行首的 ``>`` / 空格前缀并切片返回剩余
    文本（O(前缀)），不再逐字符累积整行副本。修复前对整行做 Python 级循环
    （引用行的内容可达上万字符），流式期间每帧分类一次活动行 → 单帧成本
    O(行长)、累计 O(n²)：40k 字符单行引用流式渲染实测 22s。

    前缀规则与原实现等价：``>`` 连续计数为嵌套深度（其间空格忽略），首个
    非 ``>`` 非空格字符起的剩余文本即内层内容（含其中的 ``>``）。
    """
    n = len(stripped)
    i = 0
    depth = 0
    while i < n:
        ch = stripped[i]
        if ch == '>':
            depth += 1
        elif ch != ' ':
            break
        i += 1
    return depth, stripped[i:].strip()


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


def has_block_markers(lines) -> bool:
    """正文行列表是否含块级 Markdown 标记（列表/围栏/引用/表格/标题…）。

    供容器块（告示 / 定义列表续段）判定：含块级标记时走子解析 + 完整
    Markdown 渲染，否则走逐行增量缓存（纯文本行的常见路径零额外开销）。
    解析层与 ANSI 渲染路径共用本实现。
    """
    for raw in lines:
        raw = raw or ""
        # 缩进代码块（4 空格 / Tab）：定义续段 / 容器正文中的代码块
        if raw.startswith('    ') or raw.startswith('\t'):
            return True
        s = raw.strip()
        if not s:
            continue
        first = s[0]
        if first in '>|#`~':
            return True
        if first in '-*+' and len(s) > 1 and s[1] == ' ':
            return True
        if first == '<' and len(s) > 1 and (s[1].isalpha() or s[1] == '/'):
            return True
        if s.startswith(':::') or s.startswith('!!!') or s.startswith('???'):
            return True
        if s.startswith('$$') or s == r'\[':
            return True
        if first.isdigit():
            j = 1
            while j < len(s) and s[j].isdigit():
                j += 1
            if j + 1 < len(s) and s[j] in '.):' and s[j + 1] == ' ':
                return True
        if len(s) >= 3 and first in '-=_*' and all(c in (first, ' ') for c in s):
            return True
    return False


# ── Front Matter 判定（见文件顶部 re-export） ────────────
