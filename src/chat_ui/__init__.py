"""ChatUI — 终端聊天消费者包。

React Ink-like TUI 架构（组件化设计，已拆分为 5 个子模块）：

  _components.py — 组件层
    ├── BottomBarProtocol / TuiComponent (基类)
    ├── UserMsgBlock / ThinkingBlock / AnswerBlock
    ├── ToolOutputBlock / ToolSummaryBlock
    ├── ErrorBlock / NotificationBlock / WriteLineBlock
    └── StatusLine / InputLine / CompletionPopup / SelectionMenu
  _renderer.py  — 渲染器
    ├── _RenderState  — IncrementalRenderer 生命周期管理
    ├── _RENDER_DISPATCH — 渲染命令分发表
    └── TuiRenderer     — 组件化渲染分发
  _engine.py    — 渲染引擎
    └── TuiEngine  — render 线程 + 命令队列 + 三阶段流水线
  _dispatcher.py — 事件分发器
    ├── _HANDLER_MAP     — 事件类型映射表
    └── EventDispatcher  — DisplayEvent → RenderCommand
  _consumer.py  — 消费者 API
    └── ChatUIConsumer  — 对外公开 API（含 RenderEngine/ContentRenderer 兼容别名）

  _tui.py — 兼容重导出层（1-2 版本后移除，直接 import 子模块）

基础设施模块：
  _const         — RenderCommand 枚举、Rich Style 常量
  _state         — 全局活跃实例引用 + 引用计数
  _utils         — 通用工具函数
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
import threading

_error_handler_registered: bool = False
_error_handler_lock = threading.Lock()

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


def setup_chat_ui_error_handler() -> None:
    """显式注册 ChatUIErrorHandler 到 root logger。

    替代此前 __init__.py 导入时的隐式副作用。
    幂等操作——重复调用不重复注册。
    """
    global _error_handler_registered
    with _error_handler_lock:
        if _error_handler_registered:
            return
        logging.getLogger().addHandler(ChatUIErrorHandler())
        _error_handler_registered = True

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
    "setup_chat_ui_error_handler",
]

# Deprecated 自动注册 — 后续版本移除
setup_chat_ui_error_handler()
