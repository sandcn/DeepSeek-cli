"""Core engine layer — TuiEngine, TuiRenderer, strategies.

注意：以下类已废弃，已从公开 API 移除（如需使用请直接从对应模块导入）：
  - DirectRenderStrategy（@deprecated，由 VNodeRenderStrategy 取代）
  - PhaseRenderStrategy（@deprecated，由 VNodeRenderStrategy 取代）
  - PreUpdatePhase / ContentRenderPhase / BottomBarPhase / CursorPhase（已删除 phase.py）
  - RichLiveContentRenderer（@deprecated）
"""
from .engine import TuiEngine  # noqa: F401
from .factory import create_render_strategy  # noqa: F401
from .renderer import TuiRenderer  # noqa: F401
from .strategy import (  # noqa: F401
    RenderStrategy,
    VNodeRenderStrategy,
    RenderLoop,
)


def __getattr__(name: str):
    """拦截对已废弃类的导入，提供友好错误提示。"""
    _deprecated = {
        "DirectRenderStrategy": "VNodeRenderStrategy",
        "PhaseRenderStrategy": "VNodeRenderStrategy",
        "RichLiveContentRenderer": None,
        "PreUpdatePhase": None,
        "ContentRenderPhase": None,
        "BottomBarPhase": None,
        "CursorPhase": None,
    }
    if name in _deprecated:
        replacement = _deprecated[name]
        hint = f" 请改用 {replacement}。" if replacement else " 无替代品，phase.py 已删除。"
        raise ImportError(
            f"无法从 src.chat_ui.core 导入 '{name}'：已废弃。{hint}"
            f" 如需使用可直接从 src.chat_ui.core.strategy 或 src.chat_ui.core.renderer 导入。"
        )
    raise AttributeError(f"module 'src.chat_ui.core' has no attribute '{name}'")
