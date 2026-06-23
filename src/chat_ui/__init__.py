"""ChatUI — 终端聊天消费者包。

React Ink-like TUI 架构（组件化设计）：

  _tui.py — 单模块 React Ink-like 渲染引擎
    ├── 组件层
    │   ├── UserMsgBlock / ThinkingBlock / AnswerBlock
    │   ├── ToolOutputBlock / ToolSummaryBlock
    │   ├── ErrorBlock / NotificationBlock
    │   ├── StatusLine / InputLine
    │   └── CompletionPopup / SelectionMenu
    ├── 渲染引擎
    │   ├── TuiRenderer  — 组件化渲染分发
    │   ├── TuiEngine    — render 线程 + 命令队列
    │   └── EventDispatcher — DisplayEvent → RenderCommand
    └── ChatUIConsumer  — 对外公开 API

保留模块：
  _const         — RenderCommand 枚举、Rich Style 常量（保留兼容）
  _state         — 全局活跃实例引用 + 引用计数
  _utils         — 通用工具函数（保留兼容）
  _error_handler — 日志→上屏投递
  _completion    — _apply_completion 纯函数 + _CmplHandler

公开 API：
  ChatUIConsumer       — 终端聊天消费者
  get_active_chat_ui   — 获取活跃实例
  RenderCommand        — 渲染命令枚举
  ChatUIErrorHandler   — 日志→上屏投递
  _apply_completion    — Tab 补全应用（纯函数）
  _active_consumer     — 模块级活跃实例引用
  _MAIN_LABEL          — 主 Agent 标签（供测试使用）
"""

from __future__ import annotations

import logging

# ── 常量导出 ──────────────────────────────────────
from ._const import (
    RenderCommand,
    _MAIN_LABEL,
)

# ── 全局状态导出 ──────────────────────────────────
from ._state import (
    _active_consumer,
    get_active_chat_ui,
)

# ── 错误处理 ──────────────────────────────────────
from ._error_handler import ChatUIErrorHandler
logging.getLogger().addHandler(ChatUIErrorHandler())

# ── 补全纯函数 ────────────────────────────────────
from ._completion import _apply_completion

# ── 核心 TUI（组件化架构） ─────────────────────────
from ._tui import ChatUIConsumer

__all__ = [
    "ChatUIConsumer",
    "get_active_chat_ui",
    "RenderCommand",
    "ChatUIErrorHandler",
    "_apply_completion",
    "_active_consumer",
    "_MAIN_LABEL",
]
