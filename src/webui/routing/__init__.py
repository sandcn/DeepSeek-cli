"""消息路由包 — 拆分为 context / handlers / websocket 三个模块

演进说明：
  - routing.py（452行）拆分为 routing/ 子包：
    - context.py   — ConnectionContext 连接上下文管理
    - handlers.py  — 消息类型 → handler 路由表 + 各 handler 函数
    - websocket.py — handle_websocket 主入口（心跳 + 消息循环）
    - __init__.py  — 向后兼容导出
"""

from .context import ConnectionContext
from .handlers import (
    WS_MESSAGE_HANDLERS,
    _handle_user_message,
    _handle_user_select,
    _handle_stop_generating,
    _handle_exit_command,
    _handle_get_models,
    _handle_set_model,
    _handle_delete_session,
    _handle_load_session,
    _handle_rename_session,
)
from .websocket import handle_websocket

__all__ = [
    "handle_websocket",
    "ConnectionContext",
    "WS_MESSAGE_HANDLERS",
    "_handle_user_message",
    "_handle_user_select",
    "_handle_stop_generating",
    "_handle_exit_command",
    "_handle_get_models",
    "_handle_set_model",
    "_handle_delete_session",
    "_handle_load_session",
    "_handle_rename_session",
]
