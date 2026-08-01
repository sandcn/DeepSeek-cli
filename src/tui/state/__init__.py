"""state — 统一状态管理入口（精简版）。

迁移说明（2026-07-29 TUI 重构 + 2026-08-01 ink 重构）：
  - 移除 session_state / input_state / streaming_state / tui_state_tree / agent_state
  - render_state（ChatRenderState）已并入 AppModel（src/tui/app/model.py）
  - 仅保留 consumer_registry / _collection 核心模块
"""

from __future__ import annotations

from ._collection import ThreadSafeList
from .consumer_registry import (
    _active_consumer,
    get_active_chat_ui,
    _register_consumer,
    _unregister_consumer,
)

__all__ = [
    "ThreadSafeList",
    "_active_consumer",
    "get_active_chat_ui",
    "_register_consumer",
    "_unregister_consumer",
]
