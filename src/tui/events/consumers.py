"""向后兼容存根 — 从 bus/ 重导出。

原模块已迁移至 src/tui/bus/consumers.py。
"""

from __future__ import annotations

from ..bus.consumers import (
    OutputConsumer,
    publish_output,
    publish_tool_summary,
)

__all__ = ["OutputConsumer", "publish_output", "publish_tool_summary"]
