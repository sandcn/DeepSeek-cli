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

#: HTML 标题标签 → 标题级别（块级 HTML 按 Markdown 标题语义渲染）
_HTML_HEADING_LEVELS: dict[str, int] = {
    'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6,
}

#: ``\begin{env}`` 环境直接作为公式块渲染（无需 ``$$`` 定界符）的环境集合。
#: 覆盖渲染层 ``src/renderer/ansi/_math_env.py`` 的 ``KNOWN_ENVS`` 全集（矩阵 /
#: cases / 对齐 / 多行 / 交换图 / 列格式数组等）——修复前仅列出部分环境，
#: ``\begin{cases}`` / ``\begin{matrix}`` / ``\begin{split}`` 等落到段落，
#: 环境语法完全失效（渲染为字面 ``\begin{cases}…`` 文本）。两处集合的同步由
#: ``tests/test_ansi_markdown_syntax_v9.py`` 的守护断言保证。
_DISPLAY_MATH_ENVS: frozenset[str] = frozenset({
    # ── 单列居中（CENTER_ENVS）──
    "equation", "equation*", "displaymath", "math", "dmath",
    "dgroup", "mathdisplay", "gather", "gather*", "gathered",
    "lgathered", "rgathered",
    # ── 对齐（ALIGN_ENVS）──
    "align", "align*", "aligned", "alignedat", "alignedat*",
    "alignat", "alignat*", "split", "eqnarray", "eqnarray*",
    "flalign", "flalign*", "IEEEeqnarray", "IEEEeqnarray*",
    # ── 多行（MULTLINE_ENVS）──
    "multline", "multline*", "multlined",
    # ── 矩阵（MATRIX_ENVS）──
    "matrix", "matrix*", "pmatrix", "pmatrix*", "bmatrix", "bmatrix*",
    "Bmatrix", "Bmatrix*", "vmatrix", "vmatrix*", "Vmatrix", "Vmatrix*",
    "smallmatrix",
    # ── 分段函数（CASES_ENVS）──
    "cases", "cases*", "dcases", "dcases*",
    "rcases", "rcases*", "drcases", "drcases*",
    # ── 列格式数组（COLSPEC_ENVS / WIDTH_COLSPEC_ENVS）──
    "array", "darray", "subarray",
    "tabular", "longtable", "supertabular", "tabu",
    "tabular*", "tabularx", "tabulary",
    # ── 交换图（CD_ENVS）──
    "CD", "cd",
})


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
    FRONT_MATTER = 12


#: 支持的告示类型（``> [!TYPE]`` 引用风格 与 ``!!! type`` fenced 风格共用）——
#: 类型白名单避免把 ``!!! 等等`` 之类的普通文本误判为告示。
_ADMONITION_TYPES: frozenset[str] = frozenset({
    "NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION", "CITE",
    "INFO", "SUCCESS", "QUESTION", "BUG", "DANGER", "EXAMPLE",
    "QUOTE", "ABSTRACT", "SUMMARY", "HINT", "ATTENTION", "FAILURE",
    "ERROR", "MISSING", "TODO",
})


__all__ = ["_State", "_MERMAID_KEYWORDS", "_SETEXT_HR_CHARS", "_ADMONITION_TYPES",
           "_HTML_HEADING_LEVELS", "_DISPLAY_MATH_ENVS"]
