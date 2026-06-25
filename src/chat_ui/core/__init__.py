"""Core engine layer — TuiEngine, TuiRenderer, strategies, phases.

注意：以下类已废弃，仅保留向后兼容导出：
  - DirectRenderStrategy（@deprecated，由 VNodeRenderStrategy 取代）
  - PhaseRenderStrategy（@deprecated，由 VNodeRenderStrategy 取代）
  - PreUpdatePhase / ContentRenderPhase / BottomBarPhase / CursorPhase（@deprecated）
  - RichLiveContentRenderer（@deprecated）
"""
from .engine import TuiEngine  # noqa: F401
from .factory import create_render_strategy  # noqa: F401
from .phase import (  # noqa: F401
    PreUpdatePhase,      # @deprecated
    ContentRenderPhase,  # @deprecated
    BottomBarPhase,      # @deprecated
    CursorPhase,         # @deprecated
)
from .renderer import TuiRenderer  # noqa: F401
from .strategy import (  # noqa: F401
    RenderStrategy,
    DirectRenderStrategy,  # @deprecated
    VNodeRenderStrategy,
    PhaseRenderStrategy,   # @deprecated
    RenderLoop,
)
