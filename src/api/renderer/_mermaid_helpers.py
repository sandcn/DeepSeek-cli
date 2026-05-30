"""_mermaid_helpers — Mermaid 字符级辅助函数与样式常量。

从 mermaid_renderer.py 拆分而来。
"""

from __future__ import annotations


from rich.style import Style


# ═══════════════════════════════════════════════════════════
# 样式常量
# ═══════════════════════════════════════════════════════════

_STYLE_BOX = Style(dim=True)
_STYLE_NODE = Style(bold=True, color="bright_white")
_STYLE_EDGE_LABEL = Style(dim=True, italic=True)
_STYLE_HEADER = Style(bold=True, color="cyan")
_STYLE_ARROW = Style(dim=True)
_STYLE_ACTOR = Style(bold=True, color="green")
_STYLE_NOTE = Style(dim=True, italic=True, color="yellow")
_STYLE_SUBGRAPH = Style(bold=True, color="bright_magenta")
_STYLE_FIELD = Style(dim=True, color="bright_black")
_STYLE_METHOD = Style(color="blue")
_STYLE_RELATION = Style(dim=True, color="bright_cyan")


# ═══════════════════════════════════════════════════════════
# 字符级辅助函数
# ═══════════════════════════════════════════════════════════

def _is_word_char(ch):
    """判断字符是否为单词字符（字母、数字、下划线）。"""
    return ch.isalnum() or ch == '_'


def _extract_word_ids(text):
    """从文本中提取所有单词 ID（字母/下划线开头，字母数字下划线组成）。"""
    ids = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isalpha() or text[i] == '_':
            start = i
            while i < n and _is_word_char(text[i]):
                i += 1
            ids.append(text[start:i])
        else:
            i += 1
    return ids


def _starts_with_ignore_case(s, prefix):
    """检查 s 是否以 prefix 开头（忽略大小写）。"""
    if len(s) < len(prefix):
        return False
    return s[:len(prefix)].lower() == prefix.lower()


def _is_comment_line(s):
    """检查行是否为注释（以 %% 开头）。"""
    return s.strip().startswith('%%')


def _parse_node_shape(text):
    """字符级解析一行中的节点声明，返回 [(node_id, display_text, shape_type), ...]。"""
    results = []
    i = 0
    n = len(text)
    while i < n:
        if not (text[i].isalpha() or text[i] == '_'):
            i += 1
            continue
        start = i
        while i < n and _is_word_char(text[i]):
            i += 1
        node_id = text[start:i]

        while i < n and text[i] == ' ':
            i += 1

        if i >= n:
            break

        shape = None
        display = None

        if i + 1 < n and text[i:i+2] == '[(':
            j = i + 2
            while j < n and text[j] != ')':
                j += 1
            if j < n and j + 1 < n and text[j+1] == ']':
                display = text[i+2:j]
                shape = "cylinder"
                i = j + 2
        elif text[i] == '{':
            j = i + 1
            while j < n and text[j] != '}':
                j += 1
            if j < n:
                display = text[i+1:j]
                shape = "diamond"
                i = j + 1
        elif text[i] == '(':
            j = i + 1
            while j < n and text[j] != ')':
                j += 1
            if j < n:
                display = text[i+1:j]
                shape = "round"
                i = j + 1
        elif text[i] == '[':
            if i + 1 < n and text[i+1] != '(':
                j = i + 1
                while j < n and text[j] != ']':
                    j += 1
                if j < n:
                    display = text[i+1:j]
                    shape = "square"
                    i = j + 1
            else:
                i += 1
        else:
            i += 1

        if shape and node_id:
            results.append((node_id, display, shape))

    return results


def _is_subgraph_start(s):
    """检查行是否为 subgraph 开始。"""
    return _starts_with_ignore_case(s.strip(), 'subgraph')


def _is_subgraph_end(s):
    """检查行是否为 subgraph 结束（end）。"""
    return s.strip().lower() == 'end'


def _extract_subgraph_title(s):
    """提取 subgraph 标题。"""
    s = s.strip()
    if _starts_with_ignore_case(s, 'subgraph'):
        title = s[9:].strip()
        return title if title else "subgraph"
    return None
