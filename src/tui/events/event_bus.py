"""向后兼容存根 — 从 bus/ 重导出。

原模块已迁移至 src/tui/bus/event_bus.py。
"""

from __future__ import annotations

from typing import Any, Callable

from ..bus.event_bus import DisplayEventBus
from ..bus.event_types import DisplayEvent

EventHandler = Callable[[DisplayEvent], Any]

__all__ = ["DisplayEventBus", "EventHandler"]
