"""向后兼容存根 — 从 bus/ 重导出。

原模块已迁移至 src/tui/bus/adapters.py。
"""

from __future__ import annotations

from ..bus.adapters import EventBusDisplayProxy, DisplayEventAdapter

__all__ = ["EventBusDisplayProxy", "DisplayEventAdapter"]
