"""renderer 归档 — 未使用的渲染路径

此目录包含两条未被主渲染路径使用的备选渲染子系统：

- ast/ — AST 中间表示层（ASTBuilder, ASTOptimizer, ASTRenderer 等）
- vnode/ — VNode 虚拟渲染节点 + 增量 Diff 引擎（VNodeBuilder, VNodeDiffer, VNodePatcher 等）

主渲染路径使用 IncrementalRenderer → RecursiveDescentParser → TokenPipeline → RenderEngine → OutputAdapter。

归档时间: 2026-07-11
"""
