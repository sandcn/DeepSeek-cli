"""ASTRenderer handler mixins — 按逻辑分组的核心 Handler 实现。

将 ASTRenderer 中所有 _handle_* 方法按类型提取到以下 Mixin 类：
  - _TextRenderingMixin: 文本类节点（文档、段落、标题、分隔线、空行、引用）
  - _ListRenderingMixin: 列表类节点（无序列表、有序列表、列表项、定义项）
  - _BlockRenderingMixin: 块级节点（代码、数学、Mermaid、表格、折叠、告示、HTML）

这些 Mixin 依赖宿主类 ASTRenderer 提供以下属性/方法：
  - self._output: OutputAdapter
  - self._code_theme: str
  - self._math_renderer: MathRenderer
  - self._mermaid_renderer: MermaidRenderer
  - self._inline_renderer: InlineRenderer
  - self._ctx: RenderContext
  - self._output_assembled(Text): 统一输出方法
  - self._render_inline(str) -> Text: 内联格式渲染
"""

from __future__ import annotations

import logging

from rich.syntax import Syntax
from rich.table import Table, box
from rich.text import Text
from pygments.lexers import get_lexer_by_name

from .._rendering import (
    render_heading as _render_heading_shared,
    render_blockquote as _render_blockquote_shared,
    render_list_item as _render_list_item_shared,
    render_definition_item as _render_definition_item_shared,
    render_todo_progress_bar as _render_todo_progress_bar_shared,
    render_details_header as _render_details_header_shared,
    render_details_footer as _render_details_footer_shared,
    render_admonition_header as _render_admonition_header_shared,
    render_mermaid_block as _render_mermaid_block_shared,
    render_mermaid_close as _render_mermaid_close_shared,
    render_hr as _render_hr_shared,
    render_code_fence_open, render_code_fence_close,
    render_code_block_syntax, is_todo, BULLET_SYMBOLS,
    split_by_br, render_code_title_bar,
    render_html_block_open, render_html_block_close,
)
from .._utils import cjk_display_width, parse_highlight_lines

from .types import ASTNode, NodeType

logger = logging.getLogger(__name__)


class _TextRenderingMixin:
    """文本/基础节点渲染 handler 集合。"""

    # ── 文档 & 章节 ───────────────────────────────────

    def _handle_document(self, node: ASTNode):
        """文档根节点：递归渲染所有子节点。"""
        for child in node.children:
            self.render(child)

    def _handle_section(self, node: ASTNode):
        """章节：递归渲染所有子节点。"""
        for child in node.children:
            self.render(child)

    # ── 段落 ─────────────────────────────────────────

    def _handle_paragraph(self, node: ASTNode):
        """普通段落。"""
        t = self._render_inline(node.content)
        self._output.write(t)
        self._output.write_line()

    # ── 标题 ─────────────────────────────────────────

    def _handle_heading(self, node: ASTNode):
        """标题。按 level 设置 Rich Style。"""
        level = node.meta.get("level", 1)
        text = node.content

        styled, padding = _render_heading_shared(
            text, level, self._output.width, self._render_inline,
        )
        if padding is not None:
            self._output.write_raw(" " * padding)

        self._output.write(styled)
        self._output.write_line()

    # ── 分隔线 ───────────────────────────────────────

    def _handle_hr(self, node: ASTNode):
        """分隔线。"""
        self._output.write_line(_render_hr_shared(self._output.width))

    # ── 空行 ─────────────────────────────────────────

    def _handle_empty_line(self, node: ASTNode):
        """空行。"""
        self._output.write_line()

    # ── 引用块 ───────────────────────────────────────

    def _handle_blockquote(self, node: ASTNode):
        """嵌套引用。"""
        depth = node.meta.get("depth", 1)
        assembled = _render_blockquote_shared(node.content, depth, self._render_inline)
        self._output_assembled(assembled)

        for child in node.children:
            self.render(child)


class _ListRenderingMixin:
    """列表类节点渲染 handler 集合。（支持 Todo 进度统计）"""

    # ── 无序列表 ─────────────────────────────────────

    def _handle_list(self, node: ASTNode):
        """无序列表：统一渲染所有子节点并统计 Todo 进度。"""
        self._render_list_with_todo(node, is_bullet=True)

    # ── 有序列表 ─────────────────────────────────────

    def _handle_ordered_list(self, node: ASTNode):
        """有序列表：统一渲染所有子节点并统计 Todo 进度。"""
        self._render_list_with_todo(node, is_bullet=False)

    # ── 列表批量渲染（含 Todo 进度） ─────────────────

    def _render_list_with_todo(self, node: ASTNode, is_bullet: bool):
        """批量渲染列表项并统计 Todo 进度。"""
        total_todos = 0
        done_todos = 0
        list_items: list[ASTNode] = []
        for child in node.children:
            if child.type is NodeType.LIST_ITEM:
                list_items.append(child)
                text = child.content
                marker, _ = is_todo(text)
                if marker is not None:
                    total_todos += 1
                    if marker in 'xX':
                        done_todos += 1

        for i, child in enumerate(list_items):
            depth = child.meta.get("depth", 1)
            child.meta["_bullet"] = is_bullet
            child.meta["_bullet_char"] = BULLET_SYMBOLS[min(depth - 1, len(BULLET_SYMBOLS) - 1)]
            if not is_bullet:
                child.meta["_number"] = i + 1
            self.render(child)

        if total_todos > 0:
            progress = _render_todo_progress_bar_shared(done_todos, total_todos)
            self._output.write(progress)

    # ── 列表项 ───────────────────────────────────────

    def _handle_list_item(self, node: ASTNode):
        """列表项渲染（支持 Todo ☐/☑ 检测）。"""
        depth = node.meta.get("depth", 1)
        is_bullet = node.meta.get("_bullet", True)
        text = node.content
        number = node.meta.get("_number", 1)

        assembled = _render_list_item_shared(text, depth, is_bullet, number, self._render_inline)
        self._output_assembled(assembled)

    # ── 定义项 ───────────────────────────────────────

    def _handle_definition_item(self, node: ASTNode):
        """定义列表项渲染：术语 + 定义。"""
        result = _render_definition_item_shared(
            node.meta.get("term", ""), node.content,
            node.meta.get("indent", 0), self._render_inline,
        )
        self._output_assembled(result)


class _BlockRenderingMixin:
    """块级节点渲染 handler 集合。"""

    # ── 代码块 ───────────────────────────────────────

    def _render_code_title_bar(self, title: str, lang: str) -> Text:
        """渲染代码块标题栏。"""
        return render_code_title_bar(title, lang, self._output.width)

    def _get_lexer(self, lang: str):
        """获取/缓存词法分析器。"""
        if lang not in self._code_lexers:
            try:
                self._code_lexers[lang] = get_lexer_by_name(lang)
            except Exception:
                logger.debug("词法分析器获取失败，降级为 text: lang=%s", lang)
                self._code_lexers[lang] = get_lexer_by_name("text", stripnl=False)
        return self._code_lexers[lang]

    def _handle_code_block(self, node: ASTNode):
        """代码块——AST 架构的核心优势：一次性 Syntax 高亮。"""
        source = node.content
        lang = node.meta.get("lang", "text")
        title = node.meta.get("title", "")
        attrs = node.meta.get("attrs", "")
        highlight_lines = parse_highlight_lines(attrs)
        indented = node.meta.get("indented", False)

        if title:
            t_title = self._render_code_title_bar(title, lang)
            self._output.write(t_title)
            self._output.write_line()

        self._output.write(render_code_fence_open(lang, indented, attrs))

        syntax = render_code_block_syntax(source, lang, self._code_theme, highlight_lines)
        self._output.write(syntax)
        if isinstance(syntax, Syntax):
            self._output.write_line()

        self._output.write(render_code_fence_close(indented))

    # ── 数学块 ───────────────────────────────────────

    def _handle_math_block(self, node: ASTNode):
        """数学公式块（使用 Rich Panel 美化）。"""
        source = node.content
        source = source.strip()
        if not source:
            return
        panel = self._math_renderer.render_block(source)
        self._output.write(panel)
        self._output.write_line()

    # ── Mermaid 图表块 ───────────────────────────────

    def _handle_mermaid_block(self, node: ASTNode):
        """Mermaid 图表块。"""
        source = node.content
        lang = node.meta.get("lang", "mermaid")
        t = _render_mermaid_block_shared(lang)
        self._output.write(t)
        result = self._mermaid_renderer.render(source)
        self._output.write(result)
        self._output.write_line()
        t = _render_mermaid_close_shared()
        self._output.write(t)

    # ── 表格 ─────────────────────────────────────────

    def _handle_table(self, node: ASTNode):
        """表格——Rich Table 批量构造。"""
        rows = node.meta.get("rows", None)
        alignments = node.meta.get("alignments", [])

        if rows is None and node.children:
            rows = []
            for row_node in node.children:
                if row_node.type is NodeType.TABLE_ROW:
                    row_cells = []
                    for cell_node in row_node.children:
                        if cell_node.type is NodeType.TABLE_CELL:
                            row_cells.append(cell_node.content)
                    if row_cells:
                        rows.append(row_cells)

        if not rows or not alignments:
            return

        headers = rows[0]
        data_rows = rows[1:]
        num_cols = len(headers)
        aligns = (alignments + ["left"] * num_cols)[:num_cols]

        col_min_widths = []
        for i, h in enumerate(headers):
            w = cjk_display_width(h)
            for row in data_rows:
                cell = row[i] if i < len(row) else ""
                w = max(w, cjk_display_width(cell))
            col_min_widths.append(w)

        total_est = sum(col_min_widths) + num_cols * 2 + (num_cols - 1) * 3
        terminal_width = self._output.width
        too_wide = total_est > terminal_width

        table = Table(
            show_header=True,
            header_style="bold",
            box=box.SIMPLE if too_wide else None,
            padding=(0, 1),
            collapse_padding=not too_wide,
        )

        for i, h in enumerate(headers):
            justify = aligns[i] if i < len(aligns) else "left"
            table.add_column(self._render_inline(h), justify=justify)

        for row in data_rows:
            padded = (row + [""] * num_cols)[:num_cols]
            rendered = []
            for cell in padded:
                if "<br>" in cell or "<br/>" in cell or "<br />" in cell:
                    parts = split_by_br(cell)
                    parts = [p for p in parts if p]
                    if not parts:
                        rendered.append(Text(""))
                    else:
                        cell_text = Text()
                        for pi, part in enumerate(parts):
                            if pi > 0:
                                cell_text.append("\n")
                            cell_text.append_text(self._render_inline(part.strip()))
                        rendered.append(cell_text)
                else:
                    rendered.append(self._render_inline(cell))
            table.add_row(*rendered)

        self._output.print(table)
        self._output.write_line()

    # ── 折叠块 ───────────────────────────────────────

    def _handle_details(self, node: ASTNode):
        """折叠块（<details><summary>...）。"""
        depth = node.meta.get("depth", 0)
        summary = node.meta.get("summary", "")
        assembled = _render_details_header_shared(depth, summary, self._render_inline)
        self._output_assembled(assembled)

        for child in node.children:
            self.render(child)

        self._output.write(_render_details_footer_shared(depth))

    # ── 告示块 ───────────────────────────────────────

    def _handle_admonition(self, node: ASTNode):
        """告示块（> [!NOTE/WARNING/...]）。"""
        adm_type = node.meta.get("type", "NOTE").upper()
        content = node.content or ""
        # ★ 修复（review 方向）：builder 的 _handle_admonition_close 把正文
        #   合并进 node.content——header 渲染器只应接收首行标题，修复前整个
        #   正文被塞进顶部标题行（内嵌 \n），正文行丢失。首行作标题，
        #   其余行按正文（prefix 前缀）渲染。
        lines = content.split("\n")
        title = lines[0] if lines else ""
        header, prefix, footer = _render_admonition_header_shared(
            adm_type, title, self._output.width, self._render_inline,
        )
        self._output.write(header)
        for body_line in lines[1:]:
            body_t = self._render_inline(body_line)
            assembled = Text.assemble(prefix, body_t)
            self._output_assembled(assembled)
        for child in node.children:
            child_t = self._render_inline(child.content)
            assembled = Text.assemble(prefix, child_t)
            self._output_assembled(assembled)
        self._output.write(footer)

    # ── HTML 块 ──────────────────────────────────────

    def _handle_html_block(self, node: ASTNode):
        """HTML 块级元素（含框线装饰）。"""
        tag = node.meta.get("tag", "div")

        self._output.write(render_html_block_open(tag, self._output.width))

        for child in node.children:
            if child.type is NodeType.HTML_LINE:
                content = self._render_inline(child.content)
                assembled = Text("  ")
                assembled.append_text(content)
                self._output_assembled(assembled)

        self._output.write(render_html_block_close(tag, self._output.width))

    def _handle_html_line(self, node: ASTNode):
        """HTML 行内容。"""
        content = self._render_inline(node.content)
        assembled = Text("  ")
        assembled.append_text(content)
        self._output_assembled(assembled)
