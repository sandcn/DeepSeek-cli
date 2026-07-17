"""向后兼容存根 — 从 state/ 重导出。

原模块已迁移至 src/tui/state/render_state.py。
"""

from __future__ import annotations

from ..state.render_state import _RenderState, _ReasoningState

__all__ = ["_RenderState", "_ReasoningState"]
