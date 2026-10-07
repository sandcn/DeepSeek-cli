"""_mermaid_parse — Mermaid 字符级解析辅助（无后端依赖）。

从 ``_mermaid_helpers.py`` 抽取的纯函数部分：节点/形状/子图/行的字符级
扫描，不含任何样式或 Rich 依赖。Rich 路径（``_mermaid_helpers``）与 ANSI
路径（``ansi/_mermaid_render``）共享同一实现，避免两处解析漂移。
"""

from __future__ import annotations


def _is_word_char(ch) -> bool:
    """字符是否为单词字符（字母、数字、下划线）。"""
    return ch.isalnum() or ch == "_"


def _extract_word_ids(text: str) -> list[str]:
    """从文本提取所有单词 ID（字母/下划线开头，字母数字下划线组成）。"""
    ids: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i].isalpha() or text[i] == "_":
            start = i
            while i < n and _is_word_char(text[i]):
                i += 1
            ids.append(text[start:i])
        else:
            i += 1
    return ids


def _starts_with_ignore_case(s: str, prefix: str) -> bool:
    if len(s) < len(prefix):
        return False
    return s[:len(prefix)].lower() == prefix.lower()


def _is_comment_line(s: str) -> bool:
    return s.strip().startswith("%%")


def _parse_node_shape(text: str) -> list[tuple]:
    """字符级解析一行中的节点声明，返回 [(node_id, display_text, shape_type), ...]。"""
    results: list[tuple] = []
    i = 0
    n = len(text)
    while i < n:
        if not (text[i].isalpha() or text[i] == "_"):
            i += 1
            continue
        start = i
        while i < n and _is_word_char(text[i]):
            i += 1
        node_id = text[start:i]
        while i < n and text[i] == " ":
            i += 1
        if i >= n:
            break
        shape = None
        display = None
        if i + 1 < n and text[i:i + 2] == "[(":
            j = i + 2
            while j < n and text[j] != ")":
                j += 1
            if j < n and j + 1 < n and text[j + 1] == "]":
                display = text[i + 2:j]
                shape = "cylinder"
                i = j + 2
        elif text[i] == "{":
            j = i + 1
            while j < n and text[j] != "}":
                j += 1
            if j < n:
                display = text[i + 1:j]
                shape = "diamond"
                i = j + 1
        elif text[i] == "(":
            j = i + 1
            while j < n and text[j] != ")":
                j += 1
            if j < n:
                display = text[i + 1:j]
                shape = "round"
                i = j + 1
        elif text[i] == "[":
            if i + 1 < n and text[i + 1] != "(":
                j = i + 1
                while j < n and text[j] != "]":
                    j += 1
                if j < n:
                    display = text[i + 1:j]
                    shape = "square"
                    i = j + 1
            else:
                i += 1
        else:
            i += 1
        if shape and node_id:
            results.append((node_id, display, shape))
    return results


def _is_subgraph_start(s: str) -> bool:
    return _starts_with_ignore_case(s.strip(), "subgraph")


def _is_subgraph_end(s: str) -> bool:
    return s.strip().lower() == "end"


def _extract_subgraph_title(s: str) -> str | None:
    s = s.strip()
    if _starts_with_ignore_case(s, "subgraph"):
        title = s[9:].strip()
        return title if title else "subgraph"
    return None


__all__ = [
    "_is_word_char",
    "_extract_word_ids",
    "_starts_with_ignore_case",
    "_is_comment_line",
    "_parse_node_shape",
    "_is_subgraph_start",
    "_is_subgraph_end",
    "_extract_subgraph_title",
]
