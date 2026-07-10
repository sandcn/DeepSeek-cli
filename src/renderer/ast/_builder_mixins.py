"""ASTBuilder handler mixins — 按逻辑分组的 Token 处理 Handler 实现。

将 ASTBuilder 中所有 _handle_* 方法按类型提取到以下 Mixin 类：
  - _TextBuildingMixin: 文本类 Token（段落、标题、分隔线、空行、引用、硬换行）
  - _ListBuildingMixin: 列表类 Token（列表项、定义项）
  - _BlockBuildingMixin: 块级 Token（代码、数学、Mermaid、折叠、告示、HTML、表格）

这些 Mixin 依赖宿主类 ASTBuilder 提供以下属性/方法：
  - self._stack: list[ASTNode]（节点栈）
  - self._root: ASTNode（文档根节点）
  - self._pending_list: list[ASTNode] | None（待合并列表项缓冲）
  - self._pending_list_ordered: bool
  - self._paragraph_buffer: list[str]（段落缓冲）
  - self._block_buffer: list[str]（块内容缓冲）
  - self._block_meta: dict（块元数据缓冲）
  - self._block_buffer_stack: list[list[str]]（嵌套块状态栈）
  - self._block_meta_stack: list[dict]（嵌套块 meta 栈）
  - self._emit(node, closed): 发射节点
  - self._current_parent() -> ASTNode: 当前父节点
  - self._flush_pending_list(closed): 刷出待合并列表
  - self._flush_paragraph_buffer(closed): 刷出段落缓冲
  - self._open_block(token, node_type, closed) -> ASTNode: 打开块
  - self._close_block(node_type, closed) -> ASTNode | None: 关闭块
"""

from __future__ import annotations

from .types import ASTNode, NodeType
from ..types import Token, TokenType


class _TextBuildingMixin:
    """文本类 Token handler 集合。"""

    # ── 段落 ────────────────────────────────────────────────

    def _handle_paragraph(self, token: Token) -> list[ASTNode]:
        """缓冲段落文本，等待后续非段落 Token 刷出。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._paragraph_buffer.append(token.content)
        return closed

    # ── 标题 ────────────────────────────────────────────────

    def _handle_heading(self, token: Token) -> list[ASTNode]:
        """创建 HEADING 节点（level 1-6, 可选 id）。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(
            NodeType.HEADING,
            content=token.content,
            meta={
                "level": token.meta.get("level", 1),
                "id": token.meta.get("id", ""),
            },
        )
        self._emit(node, closed)
        return closed

    # ── 分隔线 ──────────────────────────────────────────────

    def _handle_hr(self, token: Token) -> list[ASTNode]:
        """创建 HR 节点。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(NodeType.HR)
        self._emit(node, closed)
        return closed

    # ── 空行 ────────────────────────────────────────────────

    def _handle_empty_line(self, token: Token) -> list[ASTNode]:
        """创建 EMPTY_LINE 节点。空行结束段落缓冲和列表。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(NodeType.EMPTY_LINE)
        self._emit(node, closed)
        return closed

    # ── 引用块 ──────────────────────────────────────────────

    def _handle_blockquote(self, token: Token) -> list[ASTNode]:
        """创建 BLOCKQUOTE 节点（兼容旧 Token 格式）。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(
            NodeType.BLOCKQUOTE,
            content=token.content,
            meta={"depth": token.meta.get("depth", 1)},
        )
        self._emit(node, closed)
        return closed

    def _handle_blockquote_open(self, token: Token) -> list[ASTNode]:
        """BLOCKQUOTE_OPEN → 开始引用块，推入栈。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        self._block_buffer_stack.append(self._block_buffer)
        self._block_meta_stack.append(self._block_meta)
        node = ASTNode(NodeType.BLOCKQUOTE, meta=dict(token.meta))
        self._stack.append(node)
        self._block_buffer = []
        self._block_meta = dict(token.meta)
        return closed

    def _handle_blockquote_close(self, token: Token) -> list[ASTNode]:
        """BLOCKQUOTE_CLOSE → 弹出引用块。"""
        closed: list[ASTNode] = []
        self._flush_paragraph_buffer(closed)
        if len(self._stack) > 1 and self._stack[-1].type is NodeType.BLOCKQUOTE:
            node = self._stack.pop()
            if self._block_buffer:
                node.content = "\n".join(self._block_buffer)
                self._block_buffer = []
            if self._block_buffer_stack:
                self._block_buffer = self._block_buffer_stack.pop()
                self._block_meta = self._block_meta_stack.pop()
            self._emit(node, closed)
        return closed

    # ── 引用块行内容 ────────────────────────────────────────

    def _handle_blockquote_line(self, token: Token) -> list[ASTNode]:
        """BLOCKQUOTE_LINE → 追加到当前引用块的内容缓冲。"""
        self._block_buffer.append(token.content)
        return []

    # ── 硬换行 ──────────────────────────────────────────────

    def _handle_line_break(self, token: Token) -> list[ASTNode]:
        """LINE_BREAK → 追加到当前段落缓冲。"""
        self._paragraph_buffer.append("\n")
        return []


class _ListBuildingMixin:
    """列表类 Token handler 集合。"""

    # ── 列表项 ──────────────────────────────────────────────

    def _handle_list_item(self, token: Token) -> list[ASTNode]:
        """缓冲列表项，连续同类型列表项自动合并为 LIST/ORDERED_LIST 父节点。"""
        closed: list[ASTNode] = []
        is_ordered = not token.meta.get("bullet", True)

        if self._pending_list is not None and self._pending_list_ordered != is_ordered:
            self._flush_pending_list(closed)

        if self._pending_list is None:
            self._pending_list = []
            self._pending_list_ordered = is_ordered

        self._flush_paragraph_buffer(closed)

        node = ASTNode(
            NodeType.LIST_ITEM,
            content=token.content,
            meta={
                "depth": token.meta.get("depth", 1),
                "bullet": token.meta.get("bullet", True),
                "number": token.meta.get("number"),
                "start": token.meta.get("start"),
                "todo": token.meta.get("todo", False),
                "checked": token.meta.get("checked", False),
            },
        )
        self._pending_list.append(node)
        return closed

    # ── 定义列表项 ──────────────────────────────────────────

    def _handle_definition_item(self, token: Token) -> list[ASTNode]:
        """创建 DEFINITION_ITEM 节点（term 在 meta 中）。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(
            NodeType.DEFINITION_ITEM,
            content=token.content,
            meta={"term": token.meta.get("term", "")},
        )
        self._emit(node, closed)
        return closed


class _BlockBuildingMixin:
    """块级 Token handler 集合。"""

    # ── 代码块 ──────────────────────────────────────────────

    def _handle_code_fence_open(self, token: Token) -> list[ASTNode]:
        """开始代码块缓冲。"""
        closed: list[ASTNode] = []
        node = self._open_block(token, NodeType.CODE_BLOCK, closed)
        node.meta["lang"] = token.meta.get("lang", "text")
        node.meta["attrs"] = token.meta.get("attrs", "")
        node.meta["title"] = token.meta.get("title", "")
        return closed

    def _handle_code_line(self, token: Token) -> list[ASTNode]:
        """追加代码行到缓冲。"""
        self._block_buffer.append(token.content)
        return []

    def _handle_code_fence_close(self, token: Token) -> list[ASTNode]:
        """发出 CODE_BLOCK 节点（含完整源码）。"""
        closed: list[ASTNode] = []
        self._close_block(NodeType.CODE_BLOCK, closed)
        return closed

    def _handle_code_block(self, token: Token) -> list[ASTNode]:
        """直接创建 CODE_BLOCK 节点（已由 CodeBlockBatcher 预批处理）。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(
            NodeType.CODE_BLOCK,
            content=token.content,
            meta=dict(token.meta),
        )
        self._emit(node, closed)
        return closed

    # ── 数学块 ──────────────────────────────────────────────

    def _handle_math_block_open(self, token: Token) -> list[ASTNode]:
        """开始数学块缓冲。"""
        closed: list[ASTNode] = []
        self._open_block(token, NodeType.MATH_BLOCK, closed)
        return closed

    def _handle_math_line(self, token: Token) -> list[ASTNode]:
        """追加数学行到缓冲。"""
        self._block_buffer.append(token.content)
        return []

    def _handle_math_block_close(self, token: Token) -> list[ASTNode]:
        """发出 MATH_BLOCK 节点。

        注意：MathBlockContext 将源代码放在 CLOSE token 的 content 中
        （而非通过 MATH_LINE 逐行传递），因此当 _block_buffer 为空时，
        需从 CLOSE token 的 content 中读取源代码。
        """
        closed: list[ASTNode] = []
        if not self._block_buffer and token.content:
            self._block_buffer = [token.content]
            self._block_meta["source"] = token.content
        self._close_block(NodeType.MATH_BLOCK, closed)
        return closed

    # ── Mermaid 图表块 ──────────────────────────────────────

    def _handle_mermaid_block_open(self, token: Token) -> list[ASTNode]:
        """开始图表块缓冲。"""
        closed: list[ASTNode] = []
        self._open_block(token, NodeType.MERMAID_BLOCK, closed)
        return closed

    def _handle_mermaid_line(self, token: Token) -> list[ASTNode]:
        """追加图表行到缓冲。"""
        self._block_buffer.append(token.content)
        return []

    def _handle_mermaid_block_close(self, token: Token) -> list[ASTNode]:
        """发出 MERMAID_BLOCK 节点。

        注意：MermaidBlockContext 将源代码放在 CLOSE token 的 content 中
        （而非通过 MERMAID_LINE 逐行传递），因此当 _block_buffer 为空时，
        需从 CLOSE token 的 content 中读取源代码。
        """
        closed: list[ASTNode] = []
        if not self._block_buffer and token.content:
            self._block_buffer = [token.content]
        self._close_block(NodeType.MERMAID_BLOCK, closed)
        return closed

    # ── Details 折叠块 ──────────────────────────────────────

    def _handle_details_open(self, token: Token) -> list[ASTNode]:
        """开始折叠块。"""
        closed: list[ASTNode] = []
        node = self._open_block(token, NodeType.DETAILS, closed)
        node.meta["summary"] = token.meta.get("summary", "")
        node.meta["attrs"] = token.meta.get("attrs", "")
        return closed

    def _handle_details_line(self, token: Token) -> list[ASTNode]:
        """追加折叠行到缓冲。"""
        self._block_buffer.append(token.content)
        return []

    def _handle_details_close(self, token: Token) -> list[ASTNode]:
        """发出 DETAILS 节点。"""
        closed: list[ASTNode] = []
        self._close_block(NodeType.DETAILS, closed)
        return closed

    # ── Admonition 告示块 ──────────────────────────────────

    def _handle_admonition_open(self, token: Token) -> list[ASTNode]:
        """开始告示块。首行内容直接设为节点 content。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        self._block_buffer_stack.append(self._block_buffer)
        self._block_meta_stack.append(self._block_meta)
        node = ASTNode(
            NodeType.ADMONITION,
            content=token.content,
            meta={
                "type": token.meta.get("type", ""),
                "depth": token.meta.get("depth", 1),
            },
        )
        self._stack.append(node)
        self._block_buffer = []
        self._block_meta = dict(token.meta)
        return closed

    def _handle_admonition_line(self, token: Token) -> list[ASTNode]:
        """追加告示行到缓冲。"""
        self._block_buffer.append(token.content)
        return []

    def _handle_admonition_close(self, token: Token) -> list[ASTNode]:
        """发出 ADMONITION 节点。将缓冲内容合并到 content。"""
        closed: list[ASTNode] = []
        if len(self._stack) > 1 and self._stack[-1].type is NodeType.ADMONITION:
            node = self._stack[-1]
            if self._paragraph_buffer:
                para_content = "\n".join(self._paragraph_buffer)
                self._paragraph_buffer = []
                para_node = ASTNode(NodeType.PARAGRAPH, content=para_content)
                node.add_child(para_node)
            node = self._stack.pop()
            if self._block_buffer:
                buf = "\n".join(self._block_buffer)
                if node.content:
                    node.content += "\n" + buf
                elif buf:
                    node.content = buf
                self._block_buffer = []
            if self._block_buffer_stack:
                self._block_buffer = self._block_buffer_stack.pop()
                self._block_meta = self._block_meta_stack.pop()
            self._emit(node, closed)
        return closed

    # ── HTML 块 ─────────────────────────────────────────────

    def _handle_html_block_open(self, token: Token) -> list[ASTNode]:
        """开始 HTML 块（HTML_LINE 将作为子节点追加）。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(
            NodeType.HTML_BLOCK,
            meta={"tag": token.meta.get("tag", "")},
        )
        self._stack.append(node)
        return closed

    def _handle_html_block_line(self, token: Token) -> list[ASTNode]:
        """追加 HTML 行作为 HTML_LINE 子节点。"""
        line_node = ASTNode(
            NodeType.HTML_LINE,
            content=token.content,
        )
        self._current_parent().add_child(line_node)
        return []

    def _handle_html_block_close(self, token: Token) -> list[ASTNode]:
        """发出 HTML_BLOCK 节点。"""
        closed: list[ASTNode] = []
        if len(self._stack) > 1 and self._stack[-1].type is NodeType.HTML_BLOCK:
            node = self._stack.pop()
            self._emit(node, closed)
        return closed

    # ── 表格 ────────────────────────────────────────────────

    def _handle_table(self, token: Token) -> list[ASTNode]:
        """创建 TABLE 节点（rows/alignments 在 meta 中）。"""
        closed: list[ASTNode] = []
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(
            NodeType.TABLE,
            content=token.content,
            meta={
                "rows": token.meta.get("rows", []),
                "alignments": token.meta.get("alignments", []),
            },
        )
        self._emit(node, closed)
        return closed
