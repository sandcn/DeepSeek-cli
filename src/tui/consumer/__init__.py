"""src.tui.consumer — 消费者模式聊天 UI 渲染引擎。

ChatUI 消费者层，管理事件订阅、渲染管线和底部栏。

职责范围：
- ChatUIConsumer 事件订阅与渲染管线（订阅 DisplayEventBus，消费事件流）
- 渲染状态管理（RenderState、渲染引擎生命周期）
- 底部栏（BottomBar）管理
- Dispatcher 配置（事件→渲染组件映射）
- 完成提示 UI（CompletionPrompt）

Layer 层次：位于 TUI 架构顶层，依赖 events/state/components/pipeline/widgets 层。
"""

from __future__ import annotations

import logging
import threading

_error_handler_registered: bool = False
_error_handler_lock = threading.Lock()

# ── 常量导出 ──────────────────────────────────────
from ..engine.const import (
    RenderCommand,
    _MAIN_LABEL,
)

# ── 全局状态导出 ──────────────────────────────────
from ..state.consumer_registry import (
    _active_consumer,
    get_active_chat_ui,
)

# ── 错误处理 ──────────────────────────────────────
from .error_handler import ChatUIErrorHandler


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
from .completion import _apply_completion

# ── 核心 TUI（组件化架构） ─────────────────────────
from .consumer import ChatUIConsumer

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
