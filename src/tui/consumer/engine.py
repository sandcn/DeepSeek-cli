"""向后兼容存根 — 从 engine/ 重导出。

原模块已迁移至 src/tui/engine/engine.py。
"""

from __future__ import annotations

from ..engine.engine import (
    TuiEngine,
    RenderEngine,
    ContentRenderer,
    _ACTIVE_RENDER_INTERVAL,
    _CONSECUTIVE_FULL_THRESHOLD,
)

__all__ = [
    "TuiEngine", "RenderEngine", "ContentRenderer",
    "_ACTIVE_RENDER_INTERVAL", "_CONSECUTIVE_FULL_THRESHOLD",
]
