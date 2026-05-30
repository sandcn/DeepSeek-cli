"""Web UI 包 — aiohttp 驱动的浏览器聊天界面

架构演进（2026-05-15）：

  routing.py（452行）拆分为 routing/ 子包：
    routing/__init__.py  — 向后兼容导出
    routing/context.py   — ConnectionContext 连接上下文管理
    routing/handlers.py  — 消息类型 → handler 路由表 + 各 handler 函数
    routing/websocket.py — handle_websocket 主入口（心跳 + 消息循环）

  文件结构：
    server.py          — HTTP 服务器入口（run_web_server）+ 静态文件路由
    types.py           — 前端消息类型常量 + 消息构建函数
    routing/           — WebSocket 消息路由包
      __init__.py           — 向后兼容导出
      context.py            — ConnectionContext 连接上下文管理
      handlers.py           — 路由表 + 消息处理函数
      websocket.py          — handle_websocket 主入口 + 心跳
    cleanup.py         — 连接资源清理
    display.py         — WebDisplay（BaseDisplay 实现）
    bridge.py          — WebEventBridge（EventBus → WebSocket 转发）
    msg_index.py       — 消息索引分配（MsgIndexState / assign_msg_index）
    _base_sender.py    — BaseWebSocketSender（共享发送基类）
    ws_handler/        — WebSocket 连接处理包
      __init__.py           — 入口转发（核心逻辑在 routing/）
      connection.py         — 连接初始化与资源创建
      commands.py           — Web 命令处理（复用 core.commands）
      sandbox.py            — 沙盒消息处理
      edit.py               — 消息编辑处理
      utils.py              — 辅助函数与工具
"""

from .server import run_web_server
from .session import WEBChatSession
from .display import WebDisplay, pending_selects
from .bridge import WebEventBridge
from .msg_index import MsgIndexState
from ._base_sender import BaseWebSocketSender
from .types import WSMsgType
from .routing import handle_websocket, ConnectionContext
from .cleanup import cleanup_connection

__all__ = [
    "run_web_server",
    "WEBChatSession",
    "WebDisplay",
    "WebEventBridge",
    "pending_selects",
    "MsgIndexState",
    "BaseWebSocketSender",
    "WSMsgType",
    "handle_websocket",
    "ConnectionContext",
    "cleanup_connection",
]
