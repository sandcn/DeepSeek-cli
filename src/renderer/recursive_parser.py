"""recursive_parser — 递归下降 Markdown 解析器（无正则表达式）。

真正无正则的递归下降解析器：块级 + 内联均使用字符级扫描，
无任何 re.compile / re.match / re.search 调用。

架构：
  RegexFreeBlockParser（块级，行扫描 + 字符检测 + 引用块递归）
  + _InlineParser（内联，字符级递归下降）
  + ASTBuilder → ASTNode

提供：
  - parse_markdown(text) → ASTNode  一次性完整解析
  - parse_inline(text) → list[InlineNode]  内联格式解析
  - render_inline_to_text(nodes) → str  内联节点转纯文本
  - MarkdownRecursiveParser 类  可增量使用的解析器
  - RecursiveDescentParser（RegexFreeBlockParser 别名，向后兼容）
  - ParseState（向后兼容）

拆分说明：
  - inline_nodes.py              — 内联节点类型（已拆分）
  - inline_parser.py             — 内联解析器（已拆分）
  - _table_utils.py              — 表格辅助函数（已拆分）
  - _block_helpers.py            — 块级检测辅助函数和常量（已拆分）
  - _block_parser.py             — RegexFreeBlockParser 类（已拆分）

本文件包含：
  - MarkdownRecursiveParser    — 高层解析器（增量+全量）
  - parse_markdown()           — 一次性完整解析函数
  - RecursiveDescentParser     — RegexFreeBlockParser 别名
  - ParseState                 — 向后兼容的状态枚举
  - _post_process 等 AST 后处理函数
"""

from __future__ import annotations

import logging
from typing import Optional

_logger = logging.getLogger(__name__)

from ._block_parser import RegexFreeBlockParser
from .ast.builder import ASTBuilder
from .ast.types import ASTNode, NodeType
from .types import RenderContext


# ═══════════════════════════════════════════════════════════
# MarkdownRecursiveParser — 高层解析器
# ═══════════════════════════════════════════════════════════

class MarkdownRecursiveParser:
    """递归下降 Markdown 解析器（增量 + 全量）。

    纯字符级扫描，无正则表达式。

    使用方式：
      1. 一次性解析：parse("...") 或 parse_markdown("...")
      2. 增量解析：feed(...) + flush()
    """

    def __init__(self, ctx: RenderContext | None = None):
        self._token_parser = RegexFreeBlockParser(ctx=ctx)
        self._ast_builder = ASTBuilder(ctx=ctx)
        self._root: Optional[ASTNode] = None

    def feed(self, text: str) -> list[ASTNode]:
        """增量输入文本，返回已闭合的 ASTNode 列表。"""
        tokens = self._token_parser.feed(text)
        closed: list[ASTNode] = []
        for token in tokens:
            nodes = self._ast_builder.feed(token)
            closed.extend(nodes)
        return closed

    def flush(self) -> list[ASTNode]:
        """刷出缓冲区中所有未闭合的节点。"""
        tokens = self._token_parser.flush()
        closed: list[ASTNode] = []
        for token in tokens:
            nodes = self._ast_builder.feed(token)
            closed.extend(nodes)
        remaining = self._ast_builder.flush()
        closed.extend(remaining)
        return closed

    def parse(self, text: str) -> ASTNode:
        """一次性完整解析 Markdown 文本，返回 DOCUMENT ASTNode。"""
        closed = self.feed(text)
        closed.extend(self.flush())
        root = self._ast_builder.get_root()
        self._post_process(root)
        return root

    @staticmethod
    def _post_process(root: ASTNode):
        """AST 后处理：合并连续 blockquote、嵌套 blockquote、列表项续行。"""
        _merge_blockquotes(root)
        _nest_blockquotes(root)
        _merge_list_continuations(root)


# ═══════════════════════════════════════════════════════════
# AST 后处理函数
# ═══════════════════════════════════════════════════════════

def _merge_blockquotes(root: ASTNode):
    """合并连续的同深度 BLOCKQUOTE 节点。"""
    new_children: list[ASTNode] = []
    i = 0
    children = root.children

    while i < len(children):
        child = children[i]

        if child.type is NodeType.BLOCKQUOTE:
            depth = child.meta.get("depth", 1)
            group = [child]
            j = i + 1
            while j < len(children):
                next_child = children[j]
                if (next_child.type is NodeType.BLOCKQUOTE and
                        next_child.meta.get("depth", 1) == depth):
                    group.append(next_child)
                    j += 1
                else:
                    break

            if len(group) == 1:
                new_children.append(child)
            else:
                merged = ASTNode(
                    NodeType.BLOCKQUOTE,
                    content=child.content,
                    meta={"depth": depth},
                )
                for item in group:
                    if item.content.strip():
                        para = ASTNode(NodeType.PARAGRAPH, content=item.content)
                        merged.add_child(para)
                new_children.append(merged)
            i = j
        else:
            new_children.append(child)
            i += 1

    root.children = new_children


def _nest_blockquotes(root: ASTNode):
    """处理嵌套引用块。"""
    i = 0
    children = root.children
    while i < len(children):
        child = children[i]

        if child.type is NodeType.BLOCKQUOTE:
            depth = child.meta.get("depth", 1)
            j = i + 1
            while j < len(children):
                next_child = children[j]
                if (next_child.type is NodeType.BLOCKQUOTE and
                        next_child.meta.get("depth", 1) > depth):
                    child.add_child(next_child)
                    children.pop(j)
                elif next_child.type is NodeType.BLOCKQUOTE:
                    break
                else:
                    j += 1
            i += 1
        else:
            i += 1


def _merge_list_continuations(root: ASTNode):
    """合并列表项续行。"""
    result: list[ASTNode] = []
    i = 0
    children = list(root.children)
    while i < len(children):
        child = children[i]
        if child.type in (NodeType.LIST, NodeType.ORDERED_LIST) and child.children:
            if i + 1 < len(children) and children[i + 1].type is NodeType.PARAGRAPH:
                para = children[i + 1]
                last_item = child.children[-1]
                last_item.content = last_item.content + " " + para.content
                result.append(child)
                i += 2
                continue
        result.append(child)
        i += 1

    root.children = result


# ═══════════════════════════════════════════════════════════
# 向后兼容别名
# ═══════════════════════════════════════════════════════════

# RecursiveDescentParser = RegexFreeBlockParser
# 使旧代码 from .recursive_parser import RecursiveDescentParser 仍能工作
RecursiveDescentParser = RegexFreeBlockParser
