"""向后兼容存根 — 从 engine/ + state/ 重导出。

原模块已迁移至 src/tui/engine/renderer.py（TuiRenderer）
和 src/tui/state/render_state.py（_RenderState）。
"""

from __future__ import annotations

from ..engine.renderer import (
    TuiRenderer,
    register_render_command,
    _RENDER_DISPATCH,
)
from ..state.render_state import _RenderState

__all__ = ["TuiRenderer", "register_render_command", "_RENDER_DISPATCH", "_RenderState"]
