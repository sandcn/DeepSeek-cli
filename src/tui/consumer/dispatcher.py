"""向后兼容存根 — 从 engine/ 重导出。

原模块已迁移至 src/tui/engine/dispatcher.py。
"""

from __future__ import annotations

from ..engine.dispatcher import EventDispatcher, _HANDLER_MAP

__all__ = ["EventDispatcher", "_HANDLER_MAP"]
