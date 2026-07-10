"""vnode — VNode 渲染树 + 增量 Diff 引擎。

VNode（虚拟渲染节点）是 AST 和最终渲染输出之间的中间表示层，
专注于增量更新渲染，避免重复计算。

架构：
  ASTNode → VNodeBuilder → VNode 树
                            ↓
                 VNodeDiffer.diff(old, new) → list[VPatch]
                            ↓
                 VNodePatcher.apply() → 终端输出

使用方式：
  from .vnode import IncrementalVNodeRenderer
  inc_renderer = IncrementalVNodeRenderer(output_adapter)
  inc_renderer.render_nodes(ast_nodes)  # 首次渲染
  inc_renderer.render_nodes(new_nodes)  # 增量渲染（仅输出新增/变化）
"""

from __future__ import annotations

from .types import VNode, VNodeType, PatchType, VPatch, VNodeDiffResult
from .builder import VNodeBuilder
from .differ import VNodeDiffer
from .patcher import VNodePatcher
from .renderer import IncrementalVNodeRenderer

__all__ = [
    "VNode",
    "VNodeType",
    "PatchType",
    "VPatch",
    "VNodeDiffResult",
    "VNodeBuilder",
    "VNodeDiffer",
    "VNodePatcher",
    "IncrementalVNodeRenderer",
]
