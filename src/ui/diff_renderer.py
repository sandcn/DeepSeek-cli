"""
差异渲染模块 — 向后兼容存根（已迁移至 src/tui/consumer/diff_renderer）

迁移说明（2026-07-15）：
  - render_diff_to_ansi() 和 show_file_diff() 已迁移至 src/tui/consumer/diff_renderer.py
  - 外部调用点已更新，此文件保留为向后兼容存根
  - 新代码应直接导入 src.tui.consumer.diff_renderer
"""

from __future__ import annotations

from ..tui.consumer.diff_renderer import (  # type: ignore[import-unresolved]
    _BG_GREEN,
    _BG_OFF,
    _BG_RED,
    _fold_context,
    _get_highlighter,
    _inline_highlight,
    _parse_diff_hunks,
    _render_chunk,
    _render_diff_summary,
    _resolve_lexer_name,
    _syntax_hl,
    _write_diff_line,
    render_diff,
    render_diff_to_ansi,
    show_file_diff,
)

__all__ = [
    "render_diff_to_ansi",
    "show_file_diff",
    "render_diff",
    "_resolve_lexer_name",
    "_get_highlighter",
    "_syntax_hl",
    "_inline_highlight",
    "_parse_diff_hunks",
    "_fold_context",
    "_write_diff_line",
    "_render_chunk",
    "_render_diff_summary",
    "_BG_RED",
    "_BG_GREEN",
    "_BG_OFF",
]
