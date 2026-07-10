"""chat_ui — 消费者模式聊天 UI 渲染引擎

职责范围：
- ChatUIConsumer 事件订阅与渲染管线（订阅 DisplayEventBus，消费事件流）
- 渲染状态管理（RenderState、渲染引擎生命周期）
- 底部栏（BottomBar）管理
- Dispatcher 配置（事件→渲染组件映射）
- 完成提示 UI（CompletionPrompt）

依赖关系：
- 依赖 ui/ 包（BaseDisplay、DisplayEventBus 等基础设施）
- 不直接被核心层 core/ 直接引用（除 app_loop 装配层外）
- 不依赖 webui/

与 ui/ 包的边界：
- chat_ui = 消费者 + 渲染引擎（主动消费事件 → 驱动渲染）
- ui/ = 显示适配器 + 基础设施（BaseDisplay、EventBus、底层组件）
"""

from __future__ import annotations

import logging
import threading

_error_handler_registered: bool = False
_error_handler_lock = threading.Lock()

# ── 常量导出 ──────────────────────────────────────
from .const import (
    RenderCommand,
    _MAIN_LABEL,
)

# ── 全局状态导出 ──────────────────────────────────
from .state import (
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

