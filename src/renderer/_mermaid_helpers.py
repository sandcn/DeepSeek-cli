"""_mermaid_helpers — Mermaid 字符级辅助函数与样式常量（Rich 路径）。

字符级解析辅助（节点/形状/子图/行）已抽取到 ``_mermaid_parse``（无后端
依赖），本模块 re-export 保持既有调用面不变（Rich Mixin 与 ANSI 渲染共享
同一份解析实现，避免两处漂移）。
"""

from __future__ import annotations

from rich.style import Style

from ._mermaid_parse import (
    _is_word_char,
    _extract_word_ids,
    _starts_with_ignore_case,
    _is_comment_line,
    _parse_node_shape,
    _is_subgraph_start,
    _is_subgraph_end,
    _extract_subgraph_title,
)


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


__all__ = [
    "_is_word_char",
    "_extract_word_ids",
    "_starts_with_ignore_case",
    "_is_comment_line",
    "_parse_node_shape",
    "_is_subgraph_start",
    "_is_subgraph_end",
    "_extract_subgraph_title",
    "_STYLE_BOX",
    "_STYLE_NODE",
    "_STYLE_EDGE_LABEL",
    "_STYLE_HEADER",
    "_STYLE_ARROW",
    "_STYLE_ACTOR",
    "_STYLE_NOTE",
    "_STYLE_SUBGRAPH",
    "_STYLE_FIELD",
    "_STYLE_METHOD",
    "_STYLE_RELATION",
]
