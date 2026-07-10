"""ast — 流式 Markdown AST 中间表示层。

将 RecursiveDescentParser 产出的扁平 Token 流转换为树形 AST，
消除 RenderEngine 侧的跨行累积和状态管理复杂度。

架构：
  RecursiveDescentParser → ASTBuilder → ASTOptimizer → ASTRenderer  (高效路径)
                                          ↓ (可选)
                                    ASTFlattener → TokenPipeline → RenderEngine (兼容路径)

使用方式（AST 模式）：
  from .ast import ASTBuilder, ASTOptimizer, ASTRenderer, ASTFlattener
  from .ast.types import ASTNode, NodeType

  builder = ASTBuilder()
  optimizer = ASTOptimizer()
  for token in tokens:
      nodes = builder.feed(token)
  nodes += builder.flush()
  root = builder.get_root()
  optimized = optimizer.optimize(root)
  renderer.render_all(optimized.children)

迁移记录：2026-07-11 从 src/renderer/_archive/ast/ 提升至 src/renderer/ast/（重构阶段 3）。
"""

from __future__ import annotations

from .types import ASTNode, NodeType, SourceRange
from .builder import ASTBuilder
from .optimizer import ASTOptimizer
from .flatten import ASTFlattener
from .renderer import ASTRenderer

__all__ = [
    "ASTNode",
    "NodeType",
    "SourceRange",
    "ASTBuilder",
    "ASTOptimizer",
    "ASTFlattener",
    "ASTRenderer",
]
