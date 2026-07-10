"""ASTRenderer — AST 直接渲染器，消费 ASTNode 树输出 Rich 终端渲染。

与 RenderEngine 并行存在，专为 AST 架构设计。
利用树形结构的优势：
- 代码块一次性完整高亮（避免 O(n²) 逐行重解析）
- 表格批量渲染（一次构造 Rich Table）
- 列表可提前知道条目数（如 Todo 进度）
- 嵌套结构天然处理（引用、列表嵌套）

用法：
  renderer = ASTRenderer(output_adapter)
  for node in ast_nodes:
      renderer.render(node)
  renderer.render_footnotes()
"""

from __future__ import annotations

import logging

from rich.style import Style
from rich.table import Table
from rich.text import Text

from .types import ASTNode, NodeType
from ...output import OutputAdapter
from ...types import RenderContext
from ...math_renderer import MathRenderer
from ...mermaid_renderer import MermaidRenderer
from ...inline_renderer import InlineRenderer

from ..._rendering import (
    render_code_title_bar, render_code_fence_open, render_code_fence_close,
    render_code_block_syntax, style_heading,
)
from ._render_mixins import (
    _TextRenderingMixin, _ListRenderingMixin, _BlockRenderingMixin,
)


logger = logging.getLogger(__name__)


class ASTRenderer(_TextRenderingMixin, _ListRenderingMixin, _BlockRenderingMixin):
    """AST 直接渲染器——消费 ASTNode 树，输出 Rich 终端渲染。

    与 RenderEngine 并行存在，专为 AST 架构设计。
    利用树形结构的优势：
    - 代码块一次性完整高亮（避免 O(n²) 逐行重解析）
    - 表格批量渲染（一次构造 Rich Table）
    - 列表可提前知道条目数（如 Todo 进度）
    - 嵌套结构天然处理（引用、列表嵌套）

    用法：
      renderer = ASTRenderer(output_adapter)
      for node in ast_nodes:
          renderer.render(node)
      renderer.render_footnotes()
    """

    def __init__(self, output: OutputAdapter, ctx: RenderContext | None = None,
                 code_theme: str = "monokai", typing_speed: int = 1000):
        self._output = output
        self._ctx = ctx or RenderContext()
        self._code_theme = code_theme
        self._typing_speed = typing_speed
        self._math_renderer = MathRenderer()
        self._mermaid_renderer = MermaidRenderer()

        # 代码高亮缓存（lazy init）
        self._theme = None
        self._code_lexers: dict[str, object] = {}

        # 内联格式渲染器（委托给共享的 InlineRenderer）
        self._inline_renderer = InlineRenderer()

        # Handler 注册表
        self._handlers: dict[NodeType, callable] = {}
        self._register_handlers()

    def _register_handlers(self):
        """注册所有 NodeType handler。新增 NodeType 时必须在此添加对应 handler。"""
        self._handlers = {
            NodeType.DOCUMENT: self._handle_document,
            NodeType.SECTION: self._handle_section,
            NodeType.PARAGRAPH: self._handle_paragraph,
            NodeType.HEADING: self._handle_heading,
            NodeType.HR: self._handle_hr,
            NodeType.EMPTY_LINE: self._handle_empty_line,
            NodeType.BLOCKQUOTE: self._handle_blockquote,
            NodeType.LIST: self._handle_list,
            NodeType.ORDERED_LIST: self._handle_ordered_list,
            NodeType.LIST_ITEM: self._handle_list_item,
            NodeType.DEFINITION_ITEM: self._handle_definition_item,
            NodeType.CODE_BLOCK: self._handle_code_block,
            NodeType.MATH_BLOCK: self._handle_math_block,
            NodeType.MERMAID_BLOCK: self._handle_mermaid_block,
            NodeType.TABLE: self._handle_table,
            NodeType.DETAILS: self._handle_details,
            NodeType.ADMONITION: self._handle_admonition,
            NodeType.HTML_BLOCK: self._handle_html_block,
            NodeType.HTML_LINE: self._handle_html_line,
        }

    # ── 核心调度 ────────────────────────────────────────

    def render(self, node: ASTNode) -> None:
        """渲染单个 ASTNode。"""
        handler = self._handlers.get(node.type)
        if handler:
            handler(node)

    def render_all(self, nodes: list[ASTNode]) -> None:
        """渲染 ASTNode 列表。"""
        for node in nodes:
            self.render(node)

    # ── 辅助方法 ────────────────────────────────────────

    def _output_assembled(self, assembled: Text):
        """统一输出 assembled Text（打字机或即时）。"""
        if self._typing_speed > 0:
            self._output.write_typing(assembled, self._typing_speed)
        else:
            self._output.write(assembled)

    # ═══════════════════════════════════════════════════════
    # 脚注渲染
    # ═══════════════════════════════════════════════════════

    def render_footnotes(self) -> list[Text]:
        """渲染所有已收集的脚注定义。

        每条脚注末尾追加 ↩ 返回链接符号。
        """
        if not self._ctx.fn_map:
            return []
        result = Text()
        result.append(f"\n{'─' * self._output.width}\n", style=Style(dim=True))
        for i, (ref_id, content) in enumerate(sorted(self._ctx.fn_map.items()), 1):
            result.append(f"  [{i}] ", style=Style(color="bright_cyan"))
            result.append_text(self._render_inline(content))
            result.append(" ↩", style=Style(color="bright_cyan", dim=True))
            result.append("\n")
        return [result]

    # ═══════════════════════════════════════════════════════
    # 内联格式渲染
    # ═══════════════════════════════════════════════════════

    def _render_inline(self, text: str) -> Text:
        """渲染内联 Markdown 格式为 Rich Text（委托给共享 InlineRenderer）。"""
        return self._inline_renderer.render(text, self._ctx)
