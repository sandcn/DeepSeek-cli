"""ast.builder — ASTBuilder：从 Token 流构建树形 AST。

核心职责：
  1. 接收 RecursiveDescentParser 产出的 Token 流
  2. 识别成对的 OPEN/CLOSE Token，构建树形结构
  3. 自动合并连续同类块（列表、段落）
  4. 增量产出已闭合的 ASTNode
"""

from __future__ import annotations

from typing import Optional

from .types import ASTNode, NodeType, SourceRange
from ...types import Token, TokenType, RenderContext
from ._builder_mixins import (
    _TextBuildingMixin, _ListBuildingMixin, _BlockBuildingMixin,
)


class ASTBuilder(_TextBuildingMixin, _ListBuildingMixin, _BlockBuildingMixin):
    """从 Token 流构建树形 AST。

    使用方式：
      builder = ASTBuilder()
      for token in parser.feed(chunk):
          nodes = builder.feed(token)
          for node in nodes:
              ...  # 消费已闭合的 ASTNode
      for node in builder.flush():
          ...      # 刷出缓冲区
      root = builder.get_root()  # 完整文档树
    """

    def __init__(self, ctx: RenderContext | None = None):
        self._root = ASTNode(NodeType.DOCUMENT)
        self._stack: list[ASTNode] = [self._root]
        self._ctx = ctx or RenderContext()

        # 列表合并状态
        self._pending_list: list[ASTNode] | None = None
        self._pending_list_ordered: bool = False

        # 段落缓冲
        self._paragraph_buffer: list[str] = []

        # 块缓冲（用于 CODE/MATH/MERMAID/DETAILS/ADMONITION 等成对块的内容行）
        self._block_buffer: list[str] = []
        self._block_meta: dict = {}
        # 嵌套块状态栈（替代 _saved_block_buffer 动态属性，类型安全）
        self._block_buffer_stack: list[list[str]] = []
        self._block_meta_stack: list[dict] = []

        # TokenType → handler 映射表
        self._handlers: dict[TokenType, callable] = {
            TokenType.PARAGRAPH: self._handle_paragraph,
            TokenType.HEADING: self._handle_heading,
            TokenType.HR: self._handle_hr,
            TokenType.EMPTY_LINE: self._handle_empty_line,
            TokenType.BLOCKQUOTE: self._handle_blockquote,
            TokenType.BLOCKQUOTE_OPEN: self._handle_blockquote_open,
            TokenType.BLOCKQUOTE_LINE: self._handle_blockquote_line,
            TokenType.BLOCKQUOTE_CLOSE: self._handle_blockquote_close,
            TokenType.LIST_ITEM: self._handle_list_item,
            TokenType.DEFINITION_ITEM: self._handle_definition_item,
            TokenType.CODE_FENCE_OPEN: self._handle_code_fence_open,
            TokenType.CODE_LINE: self._handle_code_line,
            TokenType.CODE_FENCE_CLOSE: self._handle_code_fence_close,
            TokenType.CODE_BLOCK: self._handle_code_block,
            TokenType.MATH_BLOCK_OPEN: self._handle_math_block_open,
            TokenType.MATH_LINE: self._handle_math_line,
            TokenType.MATH_BLOCK_CLOSE: self._handle_math_block_close,
            TokenType.MERMAID_BLOCK_OPEN: self._handle_mermaid_block_open,
            TokenType.MERMAID_LINE: self._handle_mermaid_line,
            TokenType.MERMAID_BLOCK_CLOSE: self._handle_mermaid_block_close,
            TokenType.DETAILS_OPEN: self._handle_details_open,
            TokenType.DETAILS_LINE: self._handle_details_line,
            TokenType.DETAILS_CLOSE: self._handle_details_close,
            TokenType.ADMONITION_OPEN: self._handle_admonition_open,
            TokenType.ADMONITION_LINE: self._handle_admonition_line,
            TokenType.ADMONITION_CLOSE: self._handle_admonition_close,
            TokenType.HTML_BLOCK_OPEN: self._handle_html_block_open,
            TokenType.HTML_BLOCK_LINE: self._handle_html_block_line,
            TokenType.HTML_BLOCK_CLOSE: self._handle_html_block_close,
            TokenType.TABLE: self._handle_table,
            TokenType.LINE_BREAK: self._handle_line_break,
        }

    # ═══════════════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════════════

    def feed(self, token: Token) -> list[ASTNode]:
        """处理一个 Token，返回已闭合的 ASTNode 列表（增量输出）。"""
        handler = self._handlers.get(token.type)
        if handler:
            return handler(token)
        return []

    def flush(self) -> list[ASTNode]:
        """刷出缓冲区中所有未闭合的节点。"""
        closed: list[ASTNode] = []

        # 刷出待合并列表
        self._flush_pending_list(closed)

        # 刷出段落缓冲
        self._flush_paragraph_buffer(closed)

        # 刷出栈上未闭合的块级节点
        while len(self._stack) > 1:
            node = self._stack.pop()
            if self._block_buffer:
                node.content = "\n".join(self._block_buffer)
                self._block_buffer = []
            self._root.add_child(node)
            closed.append(node)

        return closed

    def get_root(self) -> ASTNode:
        """获取完整的文档树（包含所有已处理节点）。"""
        return self._root

    # ═══════════════════════════════════════════════════════════
    # 辅助方法
    # ═══════════════════════════════════════════════════════════

    def _current_parent(self) -> ASTNode:
        """获取当前父节点（栈顶）。"""
        return self._stack[-1]

    def _emit(self, node: ASTNode, closed: list[ASTNode]) -> None:
        """将节点添加到当前父节点并加入 closed 列表。"""
        self._current_parent().add_child(node)
        closed.append(node)

    def _flush_pending_list(self, closed: list[ASTNode]) -> None:
        """刷出待合并的列表项，包裹为 LIST/ORDERED_LIST 父节点。"""
        if self._pending_list is None:
            return
        parent_type = NodeType.ORDERED_LIST if self._pending_list_ordered else NodeType.LIST
        parent = ASTNode(parent_type, children=self._pending_list)
        self._pending_list = None
        self._pending_list_ordered = False
        self._emit(parent, closed)

    def _flush_paragraph_buffer(self, closed: list[ASTNode]) -> None:
        """刷出段落缓冲为一个（或多个）PARAGRAPH 节点。"""
        if not self._paragraph_buffer:
            return
        content = "\n".join(self._paragraph_buffer)
        self._paragraph_buffer = []
        node = ASTNode(NodeType.PARAGRAPH, content=content)
        self._emit(node, closed)

    def _open_block(self, token: Token, node_type: NodeType,
                    closed: list[ASTNode]) -> ASTNode:
        """打开一个块级节点，将其推入栈顶，同时刷出列表和段落缓冲。"""
        self._flush_pending_list(closed)
        self._flush_paragraph_buffer(closed)
        node = ASTNode(node_type, meta=dict(token.meta))
        # 保存当前状态到栈，支持嵌套块
        self._block_buffer_stack.append(self._block_buffer)
        self._block_meta_stack.append(self._block_meta)
        self._stack.append(node)
        self._block_buffer = []
        self._block_meta = dict(token.meta)
        return node

    def _close_block(self, node_type: NodeType,
                     closed: list[ASTNode]) -> ASTNode | None:
        """关闭栈顶指定类型的块级节点，返回该节点（无匹配返回 None）。"""
        self._flush_paragraph_buffer(closed)
        if len(self._stack) > 1 and self._stack[-1].type is node_type:
            node = self._stack.pop()
            if self._block_buffer:
                node.content = "\n".join(self._block_buffer)
                self._block_buffer = []
            # 恢复外层块的状态（通过栈）
            if self._block_buffer_stack:
                self._block_buffer = self._block_buffer_stack.pop()
                self._block_meta = self._block_meta_stack.pop()
            self._emit(node, closed)
            return node
        return None

