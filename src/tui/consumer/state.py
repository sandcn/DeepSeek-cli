"""向后兼容存根 — 从 state/ 重导出。

原模块已迁移至 src/tui/state/consumer_registry.py。
"""

from __future__ import annotations

from ..state.consumer_registry import (
    _active_consumer,
    get_active_chat_ui,
    _register_consumer,
    _unregister_consumer,
)

__all__ = [
    "_active_consumer",
    "get_active_chat_ui",
    "_register_consumer",
    "_unregister_consumer",
]
