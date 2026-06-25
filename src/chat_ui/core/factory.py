"""渲染策略工厂 — 集中管理环境变量读取和策略实例化。

从 _engine.py 拆分，将 TuiEngine._select_strategy() 提取为独立模块。
统一渲染策略：始终返回 VNodeRenderStrategy（唯一策略）。
"""

from __future__ import annotations

import logging
import os
from typing import Any, TYPE_CHECKING

from ..commands.const import _ENV_FIXED_FPS, _FIXED_FRAME_INTERVAL, _ENV_LAYERED_RENDER
from ..core.strategy import VNodeRenderStrategy

if TYPE_CHECKING:
    from ..core.renderer import TuiRenderer

_logger = logging.getLogger(__name__)


def _create_vnode_output_func(adapter):
    """创建 VNode 渲染输出函数。

    契约：每次调用输出一行文本并追加换行符。
    适用于 user_messages、notifications、errors、write_lines、tool_calls、
    tool_results 等一次性块类型的输出。流式类型（answer_block、thinking_block）
    不经过此函数。subagent_slots 直接使用 adapter.write_raw() 实现原地刷新。

    支持 str 和 StyledText 两种输入类型。
    """
    def _output(text) -> None:
        adapter.write_raw(str(text) + "\n")
    return _output


def create_render_strategy(renderer: "TuiRenderer") -> tuple[VNodeRenderStrategy, bool, Any]:
    """创建渲染策略 — 始终返回 VNodeRenderStrategy（唯一策略）。

    一次读取所有渲染相关环境变量，集中管理策略实例化（仅在 TuiEngine.__init__ 调用一次）。
    React Ink 路径时额外初始化 Hooks 运行时。

    Args:
        renderer: TuiRenderer 实例

    Returns:
        (strategy, use_fixed_fps, store)
    """
    use_fixed_fps: bool = (
        os.environ.get(_ENV_FIXED_FPS, "").strip().lower()
        in ("1", "true", "yes", "on")
    )

    if use_fixed_fps:
        _logger.info("固定帧率渲染已启用（%.0f fps）", 1.0 / _FIXED_FRAME_INTERVAL)

    # ── 始终使用 VNodeRenderStrategy ──
    from ..state.store import TuiStore
    from ..vdom.builder import build_vnode_tree
    store = TuiStore()

    # ── React Ink Feature Flag：额外初始化 Hooks 运行时 ──
    from ..react_ink import _is_enabled as _react_ink_enabled

    if _react_ink_enabled():
        from ..react_ink import get_hooks_runtime
        # 初始化 Hooks 运行时：触发全局单例创建，设置组件追踪栈。
        # 副作用：创建全局 _hooks_runtime 单例，后续所有 use_*() 调用依赖此单例。
        get_hooks_runtime()
        _logger.info("React Ink 渲染已启用（VNode + Hooks）")

    _output_func = _create_vnode_output_func(renderer.output_adapter)

    use_layered: bool = (
        os.environ.get(_ENV_LAYERED_RENDER, "0").strip().lower()
        not in ("0", "", "false", "no")
    )
    if use_layered:
        _logger.info("层级渲染已启用（CHAT_UI_LAYERED_RENDER=1）")

    _logger.info("VNode Diff 渲染已启用")
    return VNodeRenderStrategy(
        renderer, store, build_vnode_tree, _output_func,
        use_layered=use_layered,
        output_adapter=renderer.output_adapter,
    ), use_fixed_fps, store
