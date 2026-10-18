"""ast — 流式 Markdown AST 中间表示层。

将 RecursiveDescentParser 产出的扁平 Token 流转换为树形 AST，供解析与
渲染消费方使用。

架构：
  RecursiveDescentParser → ASTBuilder → ASTOptimizer
                                          ↓ (可选)
                                    ASTFlattener → TokenPipeline

使用方式：
  from .ast import ASTBuilder, ASTOptimizer, ASTFlattener
  from .ast.types import ASTNode, NodeType

  builder = ASTBuilder()
  optimizer = ASTOptimizer()
  for token in tokens:
      nodes = builder.feed(token)
  nodes += builder.flush()
  root = builder.get_root()
  optimized = optimizer.optimize(root)
"""

from __future__ import annotations

from .types import ASTNode, NodeType, SourceRange
from .builder import ASTBuilder
from .optimizer import ASTOptimizer
from .flatten import ASTFlattener

__all__ = [
    "ASTNode",
    "NodeType",
    "SourceRange",
    "ASTBuilder",
    "ASTOptimizer",
    "ASTFlattener",
]
