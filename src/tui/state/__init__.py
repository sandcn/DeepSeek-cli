"""state — 统一状态管理入口（精简版）。

迁移说明（2026-07-29 TUI 重构）：
  - 移除 session_state / input_state / streaming_state / tui_state_tree / agent_state
  - 仅保留 render_state / consumer_registry / _collection 核心模块
"""

from __future__ import annotations

from ._collection import ThreadSafeList
from .render_state import RenderState, ChatRenderState, _ReasoningState, IRenderState
from .consumer_registry import (
    _active_consumer,
    get_active_chat_ui,
    _register_consumer,
    _unregister_consumer,
)

__all__ = [
    "RenderState",
    "ChatRenderState",
    "_ReasoningState",
    "IRenderState",
    "ThreadSafeList",
    "_active_consumer",
    "get_active_chat_ui",
    "_register_consumer",
    "_unregister_consumer",
]
