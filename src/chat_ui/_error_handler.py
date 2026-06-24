"""chat_ui 错误处理模块 — 将 ERROR+ 日志投递到 ChatUI 上屏。

Layer 1 — 依赖 _state（get_active_chat_ui + 错误处理状态访问函数）。

2026-06-11 简化：内联 4 个守卫方法到 emit()。
2026-06-24 重构：线程本地重入保护移至 _state._error_handler_reentrant。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

from . import _state
from ._const import _MAX_ERROR_LENGTH
from ._utils import _truncate_msg


class ChatUIErrorHandler(logging.Handler):
    """自定义 logging Handler，捕获 ERROR+ 级别日志并投递到 ChatUI 上屏。

    延迟绑定：
      - ChatUI 实例通过 get_active_chat_ui() 延迟获取
      - ChatUI 未启动/已停止时 get_active_chat_ui() 返回 None → emit 静默跳过
    """

    def __init__(self, max_length: int = _MAX_ERROR_LENGTH):
        super().__init__(level=logging.ERROR)
        self._max_length = max_length

    def emit(self, record: logging.LogRecord) -> None:
        """格式化 ERROR+ 日志记录并投递到 ChatUI 上屏。"""
        # 守卫 1: 仅处理 ERROR/CRITICAL
        if record.levelno < logging.ERROR:
            return
        # 守卫 2: 线程重入检测
        if _state.is_error_handler_reentrant():
            return
        # 守卫 3: 同一 record 已被处理
        if getattr(record, '_chatui_reported', False):
            return

        # 格式化消息
        try:
            msg_content = record.getMessage()
        except TypeError:
            _logger.warning("ChatUIErrorHandler: 格式化失败, record=%s", record.name)
            return
        if not msg_content:
            return
        msg = _truncate_msg(f"{record.name}: {msg_content}", self._max_length)

        # 设置重入标记 → 入队 → finally 清理
        _state.set_error_handler_reentrant(True)
        try:
            consumer = _state.get_active_chat_ui()
            if consumer is not None:
                consumer.on_error(msg)
        finally:
            record._chatui_reported = True
            _state.set_error_handler_reentrant(False)

