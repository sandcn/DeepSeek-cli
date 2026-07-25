"""chat_ui 错误处理模块 — 将 ERROR+ 日志投递到 ChatUI 上屏。

Layer 1 — 依赖 _state（get_active_chat_ui）+ 内部 _handler_reentrant。

2026-06-11 简化：内联 4 个守卫方法到 emit()，合并 _handler_reentrant。
"""

from __future__ import annotations

import logging
import threading

_logger = logging.getLogger(__name__)

from ..state import consumer_registry as _consumer_registry
from ..engine.utils import _truncate_msg
from ..framework import Framework

# 线程本地重入保护（防止 emit → logger → emit 递归）
_handler_reentrant = threading.local()
# 跨线程锁（threading.local 重入保护不能防止跨线程并发）
# 使用 RLock 防止 consumer.on_error 内部触发 logging → emit 重入时的死锁
_emit_lock = threading.RLock()


class ChatUIErrorHandler(logging.Handler):
    """自定义 logging Handler，捕获 ERROR+ 级别日志并投递到 ChatUI 上屏。

    延迟绑定：
      - ChatUI 实例通过 get_active_chat_ui() 延迟获取
      - ChatUI 未启动/已停止时 get_active_chat_ui() 返回 None → emit 静默跳过
    """

    def __init__(self, max_length: int | None = None):
        super().__init__(level=logging.ERROR)
        # ── 从 TuiConfig 读取 max_error_length（优先），调用方可显式覆盖 ──
        if max_length is None:
            max_length = Framework.get_default().get_config().max_error_length
        self._max_length = max_length

    def emit(self, record: logging.LogRecord) -> None:
        """格式化 ERROR+ 日志记录并投递到 ChatUI 上屏。"""
        # 守卫 1: 仅处理 ERROR/CRITICAL
        if record.levelno < logging.ERROR:
            return
        # 守卫 2: 线程重入检测
        if getattr(_handler_reentrant, 'is_active', False):
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
        with _emit_lock:
            _handler_reentrant.is_active = True
            try:
                consumer = _consumer_registry.get_active_chat_ui()
                if consumer is not None:
                    consumer.on_error(msg)
            finally:
                record._chatui_reported = True
                _handler_reentrant.is_active = False
