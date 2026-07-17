"""向后兼容存根 — 从 bus/ 重导出。

原模块已迁移至 src/tui/bus/event_pool.py。
"""

from __future__ import annotations

from ..bus.event_pool import EventPool

__all__ = ["EventPool"]
