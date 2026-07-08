"""_rendering — 共享渲染函数包。

从 `_rendering.py` 单文件拆分为职责清晰的子模块包。
所有公开符号通过本 __init__.py 统一 re-export，调用方零改动。

子模块：
  _code.py       — 代码块渲染（围栏/高亮/词法分析器）
  _blocks.py     — 块级元素渲染（标题/引用/列表/hr/空行）
  _containers.py — 容器框架（折叠块/提示块/框线）
  _special.py    — 特殊块（Mermaid/Todo/HTML/TOC/渲染统计）
  _table.py      — 表格渲染
"""

from __future__ import annotations

# ── 从 _code 导出 ──────────────────────────────────
from ._code import (
    render_code_title_bar,
    render_code_fence_open,
    render_code_fence_close,
    render_code_block_syntax,
    _build_highlight_style,
    highlight_line,
    get_lexer,
    render_inline_code_styled,
    render_diff_line,
)

# ── 从 _blocks 导出 ────────────────────────────────
from ._blocks import (
    is_todo,
    split_by_br,
    style_heading,
    render_blockquote_prefix,
    BULLET_SYMBOLS,
    get_list_item_prefix,
    _get_heading_number,
    render_heading,
    render_blockquote,
    render_list_item,
    render_todo_progress_bar,
    render_definition_item,
    render_hr,
    render_empty_line,
)

# ── 从 _containers 导出 ──────────────────────────
from ._containers import (
    render_details_header,
    render_details_footer,
    render_admonition_header,
    render_box_open,
    render_box_line_prefix,
    render_box_close,
    render_cite_prefix,
)

# ── 从 _special 导出 ─────────────────────────────
from ._special import (
    render_mermaid_block,
    render_mermaid_close,
    get_html_tag_color,
    render_html_block_open,
    render_html_block_close,
    _build_toc_connectors,
    render_toc,
    render_render_summary,
)

# ── 从 _table 导出 ────────────────────────────────
from ._table import (
    estimate_table_width,
    build_rich_table,
)

__all__ = [
    # _code
    "render_code_title_bar", "render_code_fence_open", "render_code_fence_close",
    "render_code_block_syntax", "_build_highlight_style", "highlight_line",
    "get_lexer", "render_inline_code_styled", "render_diff_line",
    # _blocks
    "is_todo", "split_by_br", "style_heading", "render_blockquote_prefix",
    "BULLET_SYMBOLS", "get_list_item_prefix", "_get_heading_number",
    "render_heading", "render_blockquote", "render_list_item",
    "render_todo_progress_bar", "render_definition_item", "render_hr",
    "render_empty_line",
    # _containers
    "render_details_header", "render_details_footer", "render_admonition_header",
    "render_box_open", "render_box_line_prefix", "render_box_close",
    "render_cite_prefix",
    # _special
    "render_mermaid_block", "render_mermaid_close", "get_html_tag_color",
    "render_html_block_open", "render_html_block_close", "_build_toc_connectors",
    "render_toc", "render_render_summary",
    # _table
    "estimate_table_width", "build_rich_table",
]
