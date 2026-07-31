"""src.tui.consumer — 消费者模式聊天 UI 渲染引擎（向后兼容 re-export）。

迁移说明（2026-07-29 TUI 重构）：
  - ChatUIConsumer 实现已迁移至 src/tui/_consumer.py
  - ChatCommand 已迁移至 src/tui/_const.py
  - RenderCommand / FrameworkCommand 已迁移至 src/tui/_const.py
  - error_handler 已内联（ChatUIErrorHandler + setup_chat_ui_error_handler）
  - 本模块作为向后兼容的 re-export 存根
"""

from __future__ import annotations

import logging
import threading

from .._consumer import ChatUIConsumer
from ..state.consumer_registry import get_active_chat_ui, _active_consumer
from .._const import RenderCommand, FrameworkCommand, ChatCommand, truncate_error_message
from .chat_config import ChatConfig

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# ChatUIErrorHandler — 内联自已删除的 consumer/error_handler.py
# ═══════════════════════════════════════════════════════════

_handler_reentrant = threading.local()
_emit_lock = threading.RLock()


class ChatUIErrorHandler(logging.Handler):
    """自定义 logging Handler，捕获 ERROR+ 级别日志并投递到 ChatUI 上屏。"""

    def __init__(self, max_length: int | None = None):
        super().__init__(level=logging.ERROR)
        if max_length is None:
            from .._config import TuiConfig
            max_length = TuiConfig.defaults().max_error_length
        self._max_length = max_length

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR:
            return
        if getattr(_handler_reentrant, 'is_active', False):
            return
        if getattr(record, '_chatui_reported', False):
            return
        try:
            msg_content = record.getMessage()
        except TypeError:
            return
        if not msg_content:
            return
        msg = f"{record.name}: {msg_content}"
        msg = truncate_error_message(msg, self._max_length)
        with _emit_lock:
            _handler_reentrant.is_active = True
            try:
                consumer = get_active_chat_ui()
                if consumer is not None:
                    consumer.on_error(msg)
            finally:
                record._chatui_reported = True
                _handler_reentrant.is_active = False


_error_handler_registered = False
_error_handler_lock = threading.Lock()


def setup_chat_ui_error_handler() -> None:
    """显式注册 ChatUIErrorHandler 到 root logger。幂等操作。"""
    global _error_handler_registered
    with _error_handler_lock:
        if _error_handler_registered:
            return
        logging.getLogger().addHandler(ChatUIErrorHandler())
        _error_handler_registered = True


__all__ = [
    "ChatUIConsumer",
    "get_active_chat_ui",
    "RenderCommand",
    "FrameworkCommand",
    "ChatCommand",
    "ChatConfig",
    "_active_consumer",
    "setup_chat_ui_error_handler",
    "ChatUIErrorHandler",
]
