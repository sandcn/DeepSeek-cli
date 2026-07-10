"""VNodePatcher — 将 VPatch 补丁列表应用到终端输出。

VNodePatcher 消费 VNodeDiffResult（两棵 VNode 树的差异对比结果），
针对不同 PatchType 采取不同的终端输出策略：

  - INSERT:  渲染新节点并输出（增量追加）
  - UPDATE:  重新渲染并覆盖旧输出（CODE_LINE 用 \\r 覆盖当前行）
  - DELETE:  跳过（终端已输出内容无法撤回，仅从缓存移除）
  - REORDER: 终端场景降级为 DELETE + INSERT（因终端无重排能力）
  - MOVE:    同 REORDER（终端无绝对定位能力）

与 ASTRenderer 的关系：
  VNodePatcher 使用 VNode（渲染树节点）而非 ASTNode（语义树节点），
  复用共享 _rendering 模块的渲染函数，消除与 handlers/ 和 ast/renderer.py 的代码重复。

用法：
  patcher = VNodePatcher(output_adapter)
  patcher.apply_patches(result, rendered_vnodes)
"""

from __future__ import annotations

import logging
from rich.text import Text
from ...output import OutputAdapter
from ...math_renderer import MathRenderer
from ...mermaid_renderer import MermaidRenderer

from ..._rendering import (
    render_code_fence_open, render_code_fence_close, style_heading,
    render_blockquote_prefix,
    render_html_block_open, render_html_block_close,
)

from ...targets.base import RenderTarget
from .types import VNode, VNodeType, VPatch, PatchType, VNodeDiffResult
from ._patcher_mixins import (
    _PatchDispatchMixin, _RenderHandlersMixin,
)


logger = logging.getLogger(__name__)


class VNodePatcher(_PatchDispatchMixin, _RenderHandlersMixin):
    """VNode 补丁应用器——将 VPatch 列表应用到终端输出。

    针对不同补丁类型采取不同的终端输出策略：
      - INSERT: 渲染新节点并输出（增量追加）
      - UPDATE: 重新渲染并覆盖旧输出（如果可能）
      - DELETE: 跳过（终端已输出内容无法撤回）
      - REORDER/MOVE: 模拟重排（终端中转为 DELETE + INSERT）

    用法：
      patcher = VNodePatcher(output_adapter)
      patcher.apply_patches(result, vnode_map)
    """

    def __init__(self, output: OutputAdapter | RenderTarget,
                 typing_speed: int = 0, code_theme: str = "monokai"):
        # 支持 RenderTarget 和 OutputAdapter 两种构造方式
        if isinstance(output, RenderTarget):
            # RenderTarget（如 TerminalRenderTarget）→ 通过协议获取内部 OutputAdapter
            adapter = output.get_output_adapter()
            if adapter is not None:
                self._output = adapter
            else:
                # 非终端 RenderTarget（如 WebRenderTarget）→ 创建控制台模拟
                from rich.console import Console
                self._output = OutputAdapter(Console(width=120, force_terminal=True))
            self._render_target = output
        else:
            self._output = output
            self._render_target = None
        self._typing_speed = typing_speed
        self._code_theme = code_theme

        # 已渲染缓存：key → True（避免重复渲染）
        self._rendered_cache: dict[str, bool] = {}

        # 代码行语法高亮主题缓存（由 _RenderHandlersMixin._ensure_theme 使用）
        # 【Bug4 修复】预初始化 _cached_theme，消除 _ensure_theme 中 hasattr 动态检查的开销
        self._cached_theme = None

        # 从 ASTRenderer 复用内联渲染能力
        from ..._archive.ast.renderer import ASTRenderer
        self._inline_engine = ASTRenderer(
            output, typing_speed=typing_speed, code_theme=code_theme,
        )

        # 数学 / Mermaid 渲染器
        self._math_renderer = MathRenderer()
        self._mermaid_renderer = MermaidRenderer()

        # Handler 注册表
        self._handlers: dict[VNodeType, callable] = {}
        self._register_handlers()

    # ═══════════════════════════════════════════════════════
    # Handler 注册
    # ═══════════════════════════════════════════════════════

    def _register_handlers(self):
        """注册所有 VNodeType handler。新增类型必须在此添加对应 handler。"""
        self._handlers = {
            VNodeType.ROOT: self._render_root,
            VNodeType.PARAGRAPH: self._render_paragraph,
            VNodeType.HEADING: self._render_heading,
            VNodeType.HR: self._render_hr,
            VNodeType.EMPTY: self._render_empty,
            VNodeType.BLOCKQUOTE: self._render_blockquote,
            VNodeType.LIST_ITEM: self._render_list_item,
            VNodeType.DEFINITION_ITEM: self._render_definition_item,
            VNodeType.CODE_FENCE: self._render_code_fence,
            VNodeType.CODE_LINE: self._render_code_line,
            VNodeType.MATH: self._render_math,
            VNodeType.MERMAID: self._render_mermaid,
            VNodeType.TABLE: self._render_table,
            VNodeType.DETAILS: self._render_details,
            VNodeType.ADMONITION: self._render_admonition,
            VNodeType.HTML_BLOCK: self._render_html_block,
            VNodeType.HTML_LINE: self._render_html_line,
        }

    # ═══════════════════════════════════════════════════════
    # 公共接口
    # ═══════════════════════════════════════════════════════

    def apply_patches(self, result: VNodeDiffResult,
                      rendered_vnodes: dict[str, VNode]) -> None:
        """应用补丁列表到终端输出。

        Args:
            result: diff 结果（包含 patches 列表）
            rendered_vnodes: key→VNode 映射（当前已渲染的所有节点）
        """
        for patch in result.patches:
            self._apply_one(patch, rendered_vnodes)

    # ═══════════════════════════════════════════════════════
    # 补丁调度
    # ═══════════════════════════════════════════════════════

    def _apply_one(self, patch: VPatch,
                   rendered_vnodes: dict[str, VNode]) -> None:
        """应用单个补丁。"""
        dispatcher = {
            PatchType.INSERT: self._handle_insert,
            PatchType.UPDATE: self._handle_update,
            PatchType.DELETE: self._handle_delete,
            PatchType.MOVE: self._handle_reorder,
            PatchType.REORDER: self._handle_reorder,
        }
        handler = dispatcher.get(patch.type)
        if handler:
            handler(patch, rendered_vnodes)

    def _should_render(self, node: VNode) -> bool:
        """检查节点是否需要渲染（避免重复）。"""
        if node.key in self._rendered_cache:
            return False
        return True

    # ═══════════════════════════════════════════════════════
    # VNode 渲染调度
    # ═══════════════════════════════════════════════════════

    def _render_vnode(self, node: VNode) -> None:
        """渲染一个 VNode 到终端。"""
        handler = self._handlers.get(node.type)
        if handler:
            handler(node)
        else:
            logger.warning("未注册的 VNodeType handler: %s", node.type)
            # 输出 fallback 占位文本，避免内容静默丢失
            self._output.write(f"[未渲染节点: {node.type}]")

    # ═══════════════════════════════════════════════════════
    # 辅助输出方法
    # ═══════════════════════════════════════════════════════

    def _output_assembled(self, assembled: Text):
        """统一输出 assembled Text（打字机或即时）。"""
        if self._typing_speed > 0:
            self._output.write_typing(assembled, self._typing_speed)
        else:
            self._output.write(assembled)

    def _write_vnode(self, renderable) -> None:
        """输出 Rich renderable 到终端。"""
        self._output.write(renderable)

    # ═══════════════════════════════════════════════════════
    # 重置
    # ═══════════════════════════════════════════════════════

    def reset_cache(self) -> None:
        """清空渲染缓存（用于完全重新渲染场景）。"""
        self._rendered_cache.clear()
        self._updated_paragraph_keys.clear()
        # _updated_heading_keys 可能尚未初始化（mixin 惰性初始化），用 hasattr 保护
        if hasattr(self, '_updated_heading_keys'):
            self._updated_heading_keys.clear()
