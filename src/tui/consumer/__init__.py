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
                    try:
                        consumer.on_error(msg)
                    except Exception:
                        # ★ P2-2：on_error 异常不再传播至 logging.handleError
                        #   （会打印 "Logging error" 到 stderr 污染终端）——
                        #   捕获记录 warning，不重放。
                        _logger.warning(
                            "ChatUIErrorHandler 投递错误上屏失败 (name=%s): %s",
                            record.name, msg, exc_info=True,
                        )
                # ★ P2-2 语义修订：无论投递成功/失败（consumer 为 None 或
                #   on_error 抛异常）均标记 ``_chatui_reported``——修复前注释
                #   声称「留待后续 emit 重放」实际**不会重放**（emit 仅在新
                #   日志记录触发时被调用，旧 record 不会再次进入本 handler）；
                #   统一为「不重放，仅记录」：同一错误最多尝试投递一次，
                #   避免后续相关日志每次重复尝试。
                record._chatui_reported = True
            finally:
                _handler_reentrant.is_active = False


_error_handler_registered = False
_error_handler_lock = threading.Lock()


def setup_chat_ui_error_handler() -> None:
    """显式注册 ChatUIErrorHandler 到 root logger。幂等操作。

    ★ 方向2（basicConfig 静默失效修复）：**须在 ``logging.basicConfig()`` 之后
    调用**——若先向 root 添加 handler，后续 ``basicConfig`` 因 root 已有 handler
    静默不生效（level/format 配置丢失）。调用方（``src/app_init/main.py``）已
    保证顺序（先 basicConfig 再注册）。
    """
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
