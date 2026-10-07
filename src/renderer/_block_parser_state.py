"""_block_parser 共享状态与常量 — 从 _block_parser.py 拆分。

供 ``_block_parser.py``（主解析器类）与 ``_block_parser_stream.py``（流式/
块级状态 mixin）共享，避免循环导入并降低单文件体量。
"""

from __future__ import annotations

from enum import IntEnum


# Mermaid 图表内容首行关键词（用于延迟 fence 后自动识别 mermaid 块）
_MERMAID_KEYWORDS: frozenset[str] = frozenset({
    "graph", "flowchart", "sequenceDiagram", "classDiagram",
    "stateDiagram", "stateDiagram-v2", "erDiagram", "gantt",
    "pie", "gitgraph", "mindmap", "timeline", "journey",
    "block", "packet", "quadrantChart", "requirementDiagram",
    "C4Context", "C4Container", "C4Component", "C4Deployment",
    "gitGraph",
})

# Setext 标题/HR 标记字符（避免每次调用创建元组）
_SETEXT_HR_CHARS: frozenset[str] = frozenset({'-', '=', '*', '_'})


class _State(IntEnum):
    """解析器状态枚举。"""
    NORMAL = 0
    CODE_FENCE = 1
    MATH_BLOCK = 2
    DISPLAY_MATH_BLOCK = 3
    MERMAID_BLOCK = 4
    DETAILS_BLOCK = 5
    INDENTED_CODE = 6
    HTML_BLOCK = 7
    FENCED_DIV = 8
    TABLE_ACTIVE = 10
    ADMONITION_BLOCK = 11


#: 支持的告示类型（``> [!TYPE]`` 引用风格 与 ``!!! type`` fenced 风格共用）——
#: 类型白名单避免把 ``!!! 等等`` 之类的普通文本误判为告示。
_ADMONITION_TYPES: frozenset[str] = frozenset({
    "NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION", "CITE",
    "INFO", "SUCCESS", "QUESTION", "BUG", "DANGER", "EXAMPLE",
    "QUOTE", "ABSTRACT", "SUMMARY", "HINT", "ATTENTION", "FAILURE",
    "ERROR", "MISSING", "TODO",
})


__all__ = ["_State", "_MERMAID_KEYWORDS", "_SETEXT_HR_CHARS", "_ADMONITION_TYPES"]
