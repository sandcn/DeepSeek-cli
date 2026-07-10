"""renderer 归档 — 未使用的渲染路径

此目录原包含两条备选渲染子系统：

- vnode/ — 已删除（虚拟渲染节点 + 增量 Diff 引擎，零外部引用，2026-07-11）
- ast/  — 已迁移至 src/renderer/ast/（由 recursive_parser.py 活跃引用，2026-07-11）

主渲染路径使用 IncrementalRenderer → RecursiveDescentParser → TokenPipeline → RenderEngine → OutputAdapter。

归档时间: 2026-07-11
"""
