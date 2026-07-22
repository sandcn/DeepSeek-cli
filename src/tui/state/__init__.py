"""state — 统一状态管理入口。

所有状态容器/存储通过此模块对外暴露。
state/ 不依赖任何业务模块（纯数据）。

模块层级（由底向上）：
  session_state.py     — UISessionState 不可变值对象
  input_state.py       — InputState Esc 双击检测
  streaming_state.py   — StreamingState 流式输出临时状态
  tui_state_tree.py    — TUIStateTree 聚合容器
  agent_state.py       — AgentStateStore 多 Agent 状态
  render_state.py      — RenderState/ChatRenderState/_RenderState 渲染器生命周期
  consumer_registry.py — 全局活跃消费者注册
"""

from __future__ import annotations

from .session_state import UISessionState
from .input_state import InputState
from .streaming_state import StreamingState
from .tui_state_tree import TUIStateTree
from .agent_state import AgentStateStore, AgentSlot, ToolRecord
from .render_state import RenderState, ChatRenderState, _RenderState, _ReasoningState, IRenderState
from .consumer_registry import (
    _active_consumer,
    get_active_chat_ui,
    _register_consumer,
    _unregister_consumer,
)

__all__ = [
    "UISessionState",
    "InputState",
    "StreamingState",
    "TUIStateTree",
    "AgentStateStore",
    "AgentSlot",
    "ToolRecord",
    "RenderState",
    "ChatRenderState",
    "_RenderState",
    "_ReasoningState",
    "IRenderState",
    "_active_consumer",
    "get_active_chat_ui",
    "_register_consumer",
    "_unregister_consumer",
]
