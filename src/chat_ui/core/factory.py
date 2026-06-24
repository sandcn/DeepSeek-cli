"""渲染策略工厂 — 集中管理环境变量读取和策略实例化。

从 _engine.py 拆分，将 TuiEngine._select_strategy() 提取为独立模块。
"""

from __future__ import annotations

import logging
import os
from typing import Any, TYPE_CHECKING

from ..commands.const import _ENV_FIXED_FPS, _FIXED_FRAME_INTERVAL
from ..core.strategy import (
    RenderStrategy, DirectRenderStrategy, VNodeRenderStrategy, PhaseRenderStrategy
)

if TYPE_CHECKING:
    from ..core.renderer import TuiRenderer

_logger = logging.getLogger(__name__)


def _create_vnode_output_func(adapter):
    """创建 VNode 渲染输出函数。

    契约：每次调用输出一行文本并追加换行符。
    适用于 user_messages、tool_outputs、notifications、errors、write_lines 等
    一次性块类型的输出。流式类型（answer_block、thinking_block）不经过此函数。
    """
    def _output(text: str) -> None:
        adapter.write_raw(text + "\n")
    return _output


def create_render_strategy(renderer: "TuiRenderer") -> tuple[RenderStrategy, bool, bool, Any]:
    """根据环境变量选择并创建渲染策略。

    一次读取所有渲染相关环境变量，选择渲染策略（仅在 TuiEngine.__init__ 调用一次）。
    环境变量读取集中于此函数，不再散落在渲染循环中。

    Phase 管线优先于 VNode：当 CHAT_UI_RENDER_PHASES=1 时忽略 CHAT_UI_RENDER_USE_VNODE。

    Args:
        renderer: TuiRenderer 实例

    Returns:
        (strategy, use_fixed_fps, use_phases, store)
    """
    use_vnode: bool = (
        os.environ.get("CHAT_UI_RENDER_USE_VNODE", "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    use_fixed_fps: bool = (
        os.environ.get(_ENV_FIXED_FPS, "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    use_phases: bool = (
        os.environ.get("CHAT_UI_RENDER_PHASES", "").strip().lower()
        in ("1", "true", "yes", "on")
    )

    if use_fixed_fps:
        _logger.info("固定帧率渲染已启用（%.0f fps）", 1.0 / _FIXED_FRAME_INTERVAL)

    # ── React Ink Feature Flag（最高优先级）──
    from src.chat_ui.react_ink import _is_enabled as _react_ink_enabled

    if _react_ink_enabled():
        from ..state.store import TuiStore
        from ..vdom.builder import build_vnode_tree
        from src.chat_ui.react_ink import get_hooks_runtime
        store = TuiStore()
        # 初始化 Hooks 运行时：触发全局单例创建，设置组件追踪栈。
        # 副作用：创建全局 _hooks_runtime 单例，后续所有 use_*() 调用依赖此单例。
        get_hooks_runtime()
        _logger.info("React Ink 渲染已启用（VNode + Hooks）")

        _output_func = _create_vnode_output_func(renderer.output_adapter)

        return VNodeRenderStrategy(
            renderer, store, build_vnode_tree, _output_func,
        ), use_fixed_fps, use_phases, store

    # 选择渲染策略（Phase 管线优先于 VNode）
    if use_phases:
        from ..core.phase import (
            PreUpdatePhase, ContentRenderPhase, BottomBarPhase, CursorPhase
        )
        phases = [
            PreUpdatePhase(),
            ContentRenderPhase(renderer),
            BottomBarPhase(),
            CursorPhase(),
        ]
        _logger.info("可插拔渲染管线已启用（%d 个 Phase）", len(phases))
        return PhaseRenderStrategy(renderer, phases, store=None), use_fixed_fps, use_phases, None
    elif use_vnode:
        from ..state.store import TuiStore
        from ..vdom.builder import build_vnode_tree
        store = TuiStore()
        _logger.info("VNode Diff 渲染已启用")

        def _output_func(text: str) -> None:
            renderer.output_adapter.write(text)

        return VNodeRenderStrategy(
            renderer, store, build_vnode_tree, _output_func,
        ), use_fixed_fps, use_phases, store
    else:
        return DirectRenderStrategy(renderer), use_fixed_fps, use_phases, None
