"""vnode.renderer — IncrementalVNodeRenderer：增量 VNode 渲染引擎。

整合 VNodeBuilder → VNodeDiffer → VNodePatcher 三阶段，
为流式 Markdown 渲染提供增量更新能力。

与 IncrementalRenderer（三层管道门面）的关系：
  - IncrementalRenderer 是整体门面（Parser → AST → 渲染）
  - IncrementalVNodeRenderer 是 AST 层之后的增量渲染引擎
  - 两者可组合使用：IncrementalRenderer (AST模式) → IncrementalVNodeRenderer

用法：
  from .vnode import IncrementalVNodeRenderer
  renderer = IncrementalVNodeRenderer(output_adapter)
  renderer.render_nodes(ast_nodes)       # 首次渲染
  renderer.render_nodes(more_nodes)      # 增量渲染（只输出新增部分）
  renderer.render_footnotes()
"""

from __future__ import annotations

from .types import VNode, VNodeType, VNodeDiffResult
from .builder import VNodeBuilder
from .differ import VNodeDiffer
from .patcher import VNodePatcher
from ...output import OutputAdapter
from ...types import RenderContext
from ..._archive.ast.types import ASTNode
from ...targets.base import RenderTarget
from ...targets.terminal import TerminalRenderTarget


class IncrementalVNodeRenderer:
    """增量 VNode 渲染引擎。

    维护上一次渲染的 VNode 树，每次新节点到达时：
      1. 用 VNodeBuilder 将 ASTNode 转为 VNode
      2. 用 VNodeDiffer 对比新旧 VNode 树
      3. 用 VNodePatcher 将补丁应用到终端

    核心优势：
      - 避免重复渲染已输出的内容
      - 代码行级增量（新增代码行只渲染新增行）
      - 零重复计算（diff 自动跳过未变化节点）
    """

    def __init__(self, output: OutputAdapter | RenderTarget,
                 ctx: RenderContext | None = None,
                 code_theme: str = "monokai", typing_speed: int = 0):
        # 支持 RenderTarget 和 OutputAdapter 两种构造方式
        if isinstance(output, RenderTarget):
            self._render_target = output
            if isinstance(output, TerminalRenderTarget) and hasattr(output, '_output'):
                self._output = output._output
            else:
                # WebRenderTarget 等非终端目标：创建控制台输出（用于 VNodePatcher）
                from rich.console import Console
                self._output = OutputAdapter(Console(width=120, force_terminal=True))
        else:
            self._output = output
            self._render_target = None
        self._ctx = ctx or RenderContext()
        self._code_theme = code_theme
        self._typing_speed = typing_speed

        # 组件
        self._builder = VNodeBuilder()
        self._differ = VNodeDiffer()
        self._patcher = VNodePatcher(
            output, typing_speed=typing_speed, code_theme=code_theme,
        )

        # 状态
        self._previous_root: VNode | None = None
        self._rendered_vnodes: dict[str, VNode] = {}
        self._has_rendered = False

    # ═══════════════════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════════════════

    def render_nodes(self, nodes: list[ASTNode]) -> VNodeDiffResult:
        """增量渲染 ASTNode 列表。

        首次调用：构建 VNode 树并全部渲染。
        后续调用：diff 新旧 VNode 树，只渲染变化部分。

        Args:
            nodes: ASTNode 列表（通常来自 ASTBuilder.feed() 返回的已闭合节点）

        Returns:
            本次渲染的差异结果（可用于调试/统计）
        """
        if not nodes:
            return VNodeDiffResult()

        # 1. 构建新 VNode 树
        vnodes = self._builder.build_all(nodes)
        new_root = VNode(
            VNodeType.ROOT,
            key="root",
            children=vnodes,
        )

        # 2. diff 新旧树
        result = self._differ.diff(self._previous_root, new_root)

        # 3. 应用补丁
        if result.has_changes():
            self._patcher.apply_patches(result, self._rendered_vnodes)

        # 4. 保存新树作为下一次对比基准
        self._previous_root = new_root
        self._has_rendered = True

        return result

    def render_node(self, node: ASTNode) -> VNodeDiffResult:
        """渲染单个 ASTNode。"""
        return self.render_nodes([node])

    def render_footnotes(self) -> None:
        """渲染脚注定义（委托给 patcher 的内联引擎）。"""
        footnotes = self._patcher._inline_engine.render_footnotes()
        for fn_text in footnotes:
            self._output.write(fn_text)

    def reset(self) -> None:
        """重置渲染状态（清空缓存的 VNode 树和补丁历史）。"""
        self._previous_root = None
        self._rendered_vnodes.clear()
        self._has_rendered = False
        self._patcher._rendered_cache.clear()

    @property
    def stats(self) -> dict:
        """返回渲染统计信息。"""
        return {
            "has_rendered": self._has_rendered,
            "cached_vnodes": len(self._rendered_vnodes),
            "previous_root": self._previous_root is not None,
        }
