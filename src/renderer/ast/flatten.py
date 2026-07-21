"""flatten — AST → Token 流展平器。

将树形 ASTNode 展平为 Token 序列，与现有 RenderEngine 兼容。
用于渐进式迁移：启用 AST 层的同时，渲染仍走现有 Engine。

用法：
  flattener = ASTFlattener()
  tokens = flattener.flatten(ast_node)
  # tokens 可直接送入 engine.render(token) 或 pipeline.process(tokens, ctx)
"""

from __future__ import annotations

from .types import ASTNode, NodeType
from ..types import Token, TokenType


class ASTFlattener:
    """AST → Token 流展平器。

    将树形 ASTNode 展平为 Token 序列，与现有 RenderEngine 兼容。
    用于渐进式迁移：启用 AST 层的同时，渲染仍走现有 Engine。

    用法：
      flattener = ASTFlattener()
      tokens = flattener.flatten(ast_node)
      # tokens 可直接送入 engine.render(token) 或 pipeline.process(tokens, ctx)
    """

    def __init__(self):
        # NodeType → handler 分发表（替代 getattr 反射）
        self._handlers: dict[NodeType, callable] = {
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
            NodeType.TEXT: self._handle_text,
        }

    def _flatten_node(self, node: ASTNode) -> list[Token]:
        """递归展平单个节点，按 NodeType 派发到对应 handler。"""
        handler = self._handlers.get(node.type)
        if handler is not None:
            return handler(node)
        # 未知节点类型 → 安全降级，记录日志
        import logging
        logging.getLogger(__name__).warning(
            "ASTFlattener：未知节点类型 %s，已跳过", node.type.name)
        return []

    # ══════════════════════════════════════════════════════
    # 文档结构
    # ══════════════════════════════════════════════════════

    def _handle_document(self, node: ASTNode) -> list[Token]:
        """DOCUMENT → 递归展平所有子节点。"""
        return self._flatten_children(node)

    def _handle_section(self, node: ASTNode) -> list[Token]:
        """SECTION → 递归展平所有子节点（HEADING + 后续块）。"""
        return self._flatten_children(node)

    # ══════════════════════════════════════════════════════
    # 块级基础
    # ══════════════════════════════════════════════════════

    def _handle_paragraph(self, node: ASTNode) -> list[Token]:
        """PARAGRAPH → PARAGRAPH(content)。"""
        return [Token(TokenType.PARAGRAPH, node.content, dict(node.meta))]

    def _handle_heading(self, node: ASTNode) -> list[Token]:
        """HEADING → HEADING(content, meta={level, id, ...})。"""
        meta = dict(node.meta)
        return [Token(TokenType.HEADING, node.content, meta)]

    def _handle_hr(self, node: ASTNode) -> list[Token]:
        """HR → HR。"""
        return [Token(TokenType.HR, node.content, dict(node.meta))]

    def _handle_empty_line(self, node: ASTNode) -> list[Token]:
        """EMPTY_LINE → EMPTY_LINE。"""
        return [Token(TokenType.EMPTY_LINE, node.content, dict(node.meta))]

    # ══════════════════════════════════════════════════════
    # 引用
    # ══════════════════════════════════════════════════════

    def _handle_blockquote(self, node: ASTNode) -> list[Token]:
        """BLOCKQUOTE → BLOCKQUOTE_OPEN + (BLOCKQUOTE_LINE × N) + BLOCKQUOTE_CLOSE。"""
        meta = dict(node.meta)
        tokens: list[Token] = [
            Token(TokenType.BLOCKQUOTE_OPEN, '', meta),
        ]
        # 如果有子节点，递归展平子节点
        if node.children:
            for child in node.children:
                tokens.extend(self._flatten_node(child))
        # 如果有 content（无子节点时的纯文本）
        if node.content and not node.children:
            for line in node.content.split('\n'):
                tokens.append(Token(TokenType.BLOCKQUOTE_LINE, line, dict(meta)))
        tokens.append(Token(TokenType.BLOCKQUOTE_CLOSE, '', dict(meta)))
        return tokens

    # ══════════════════════════════════════════════════════
    # 列表
    # ══════════════════════════════════════════════════════

    def _handle_list(self, node: ASTNode) -> list[Token]:
        """LIST → 递归展平每个子节点（LIST_ITEM → LIST_ITEM）。"""
        return self._flatten_children(node)

    def _handle_ordered_list(self, node: ASTNode) -> list[Token]:
        """ORDERED_LIST → 递归展平每个子节点，注入 bullet=False。"""
        tokens: list[Token] = []
        for child in node.children:
            if child.type is NodeType.LIST_ITEM:
                # 为有序列表项注入 bullet=False
                item_meta = dict(child.meta)
                item_meta['bullet'] = False
                adjusted = ASTNode(
                    type=NodeType.LIST_ITEM,
                    content=child.content,
                    meta=item_meta,
                    children=list(child.children),
                )
                tokens.extend(self._flatten_node(adjusted))
            else:
                tokens.extend(self._flatten_node(child))
        return tokens

    def _handle_list_item(self, node: ASTNode) -> list[Token]:
        """LIST_ITEM → LIST_ITEM(content, meta={depth, bullet, number})。"""
        meta = dict(node.meta)
        return [Token(TokenType.LIST_ITEM, node.content, meta)]

    def _handle_definition_item(self, node: ASTNode) -> list[Token]:
        """DEFINITION_ITEM → DEFINITION_ITEM(content, meta={term, indent})。"""
        meta = dict(node.meta)
        return [Token(TokenType.DEFINITION_ITEM, node.content, meta)]

    # ══════════════════════════════════════════════════════
    # 代码块
    # ══════════════════════════════════════════════════════

    def _handle_code_block(self, node: ASTNode) -> list[Token]:
        """CODE_BLOCK → CODE_FENCE_OPEN + (CODE_LINE × N) + CODE_FENCE_CLOSE。

        content 按行拆分为 CODE_LINE Token，meta（lang/attrs/title）透传给 OPEN/CLOSE。
        """
        meta = dict(node.meta)
        lines = node.content.split('\n') if node.content else ['']
        tokens: list[Token] = [
            Token(TokenType.CODE_FENCE_OPEN, '', meta),
        ]
        for line in lines:
            tokens.append(Token(TokenType.CODE_LINE, line))
        tokens.append(Token(TokenType.CODE_FENCE_CLOSE, '', dict(meta)))
        return tokens

    # ══════════════════════════════════════════════════════
    # 数学块
    # ══════════════════════════════════════════════════════

    def _handle_math_block(self, node: ASTNode) -> list[Token]:
        """MATH_BLOCK → MATH_BLOCK_OPEN + MATH_BLOCK_CLOSE（source 在 meta 中）。"""
        meta = dict(node.meta)
        return [
            Token(TokenType.MATH_BLOCK_OPEN, '', meta),
            Token(TokenType.MATH_BLOCK_CLOSE, node.content, meta),
        ]

    # ══════════════════════════════════════════════════════
    # Mermaid 图表
    # ══════════════════════════════════════════════════════

    def _handle_mermaid_block(self, node: ASTNode) -> list[Token]:
        """MERMAID_BLOCK → MERMAID_BLOCK_OPEN + (MERMAID_LINE × N) + MERMAID_BLOCK_CLOSE。

        content 按行拆分为 MERMAID_LINE Token。
        """
        meta = dict(node.meta)
        lines = node.content.split('\n') if node.content else ['']
        tokens: list[Token] = [
            Token(TokenType.MERMAID_BLOCK_OPEN, '', meta),
        ]
        for line in lines:
            tokens.append(Token(TokenType.MERMAID_LINE, line))
        tokens.append(Token(TokenType.MERMAID_BLOCK_CLOSE, '', dict(meta)))
        return tokens

    # ══════════════════════════════════════════════════════
    # 表格
    # ══════════════════════════════════════════════════════

    def _handle_table(self, node: ASTNode) -> list[Token]:
        """TABLE → TABLE(meta={rows, alignments})。

        rows/alignments 从 node.meta 透传给 Token.meta。
        """
        meta = dict(node.meta)
        return [Token(TokenType.TABLE, node.content, meta)]

    # ══════════════════════════════════════════════════════
    # 折叠块
    # ══════════════════════════════════════════════════════

    def _handle_details(self, node: ASTNode) -> list[Token]:
        """DETAILS → DETAILS_OPEN + (DETAILS_LINE × N) + DETAILS_CLOSE。

        content 按行拆分为 DETAILS_LINE Token。
        """
        meta = dict(node.meta)
        lines = node.content.split('\n') if node.content else ['']
        tokens: list[Token] = [
            Token(TokenType.DETAILS_OPEN, '', meta),
        ]
        for line in lines:
            tokens.append(Token(TokenType.DETAILS_LINE, line))
        tokens.append(Token(TokenType.DETAILS_CLOSE, '', dict(meta)))
        return tokens

    # ══════════════════════════════════════════════════════
    # 告示块
    # ══════════════════════════════════════════════════════

    def _handle_admonition(self, node: ASTNode) -> list[Token]:
        """ADMONITION → ADMONITION_OPEN + (ADMONITION_LINE × N) + ADMONITION_CLOSE。

        content 按行拆分为 ADMONITION_LINE Token。
        """
        meta = dict(node.meta)
        lines = node.content.split('\n') if node.content else ['']
        tokens: list[Token] = [
            Token(TokenType.ADMONITION_OPEN, '', meta),
        ]
        for line in lines:
            tokens.append(Token(TokenType.ADMONITION_LINE, line))
        tokens.append(Token(TokenType.ADMONITION_CLOSE, '', dict(meta)))
        return tokens

    # ══════════════════════════════════════════════════════
    # HTML 块
    # ══════════════════════════════════════════════════════

    def _handle_html_block(self, node: ASTNode) -> list[Token]:
        """HTML_BLOCK → HTML_BLOCK_OPEN + (HTML_BLOCK_LINE × N) + HTML_BLOCK_CLOSE。

        优先从 HTML_LINE 子节点提取内容（更精确）；无 children 时从 content 按行拆分。
        """
        meta = dict(node.meta)
        tokens: list[Token] = [
            Token(TokenType.HTML_BLOCK_OPEN, '', meta),
        ]
        if node.children:
            # 优先从 HTML_LINE 子节点提取内容（更精确）
            for child in node.children:
                if child.type is NodeType.HTML_LINE:
                    tokens.append(Token(
                        TokenType.HTML_BLOCK_LINE, child.content, dict(child.meta)))
        elif node.content:
            lines = node.content.split('\n')
            for line in lines:
                tokens.append(Token(TokenType.HTML_BLOCK_LINE, line))
        tokens.append(Token(TokenType.HTML_BLOCK_CLOSE, '', dict(meta)))
        return tokens

    # ══════════════════════════════════════════════════════
    # 内联（叶子）
    # ══════════════════════════════════════════════════════

    def _handle_text(self, node: ASTNode) -> list[Token]:
        """TEXT → 不单独生成 Token（作为父节点的 content 整体输出）。"""
        return []

    # ══════════════════════════════════════════════════════
    # 内部工具
    # ══════════════════════════════════════════════════════

    def _flatten_children(self, node: ASTNode) -> list[Token]:
        """递归展平所有子节点。"""
        tokens: list[Token] = []
        for child in node.children:
            tokens.extend(self._flatten_node(child))
        return tokens
