"""chat_ui 错误处理模块 — 将 ERROR+ 日志投递到 ChatUI 上屏。

Layer 1 — 依赖 _reentrant（_handler_reentrant）+ _state（get_active_chat_ui）。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

from ._reentrant import _handler_reentrant
from . import _state
from ._const import _MAX_ERROR_LENGTH
from ._utils import _truncate_msg


class ChatUIErrorHandler(logging.Handler):
    """自定义 logging Handler，捕获 ERROR+ 级别日志并投递到 ChatUI 上屏。

    通过模块级 _error_handler 实例注册到 root logger，
    在 emit() 中格式化 log record 并调用 get_active_chat_ui().on_error()。
    设计为纯入队操作（不 I/O），线程安全。

    防自引用循环保护（三层）：
      1. 线程本地重入标记 — emit 入口设置 _handler_reentrant.is_active，防止
         同一线程中 emit → on_error → logger → emit 递归
      2. record._chatui_reported 标记 — 在调用 on_error 前设置，防止同一
         record 被多个 handler 或跨线程二次处理
      3. on_error 自身仅执行队列入队操作，不产生日志调用
      三层保护确保 handler emit 绝不触发 logger → emit 死循环。

    延迟绑定：
      - ChatUI 实例通过 get_active_chat_ui() 延迟获取
      - ChatUI 未启动/已停止时 get_active_chat_ui() 返回 None → emit 静默跳过
    """

    def __init__(self, max_length: int = _MAX_ERROR_LENGTH):
        super().__init__(level=logging.ERROR)
        self._max_length = max_length

    # ── 守卫条件提取 ─────────────────────────────────
    # 以下方法将 emit() 中的行内守卫条件提取为命名方法，
    # 提升可读性和可测试性。

    @staticmethod
    def _is_below_error_level(record: logging.LogRecord) -> bool:
        """记录级别低于 ERROR 时跳过（防御纵深：即使 emit 被直接调用）。

        仅处理 ERROR/CRITICAL 级别，WARNING/INFO/DEBUG 跳过。
        """
        return record.levelno < logging.ERROR

    @staticmethod
    def _is_already_reported(record: logging.LogRecord) -> bool:
        """记录已被标记为已报告（跨 handler / 跨线程保护）。

        检查 record._chatui_reported 属性，防止同一 record
        被多个 ChatUIErrorHandler 实例或跨线程二次处理。
        """
        return getattr(record, '_chatui_reported', False)

    def _is_reentrant(self) -> bool:
        """线程重入检测（同线程 emit 递归阻断）。

        检查 _handler_reentrant.is_active，防止
        emit → on_error → logger → emit 递归。
        """
        return getattr(_handler_reentrant, 'is_active', False)

    def _try_format_message(self, record: logging.LogRecord) -> str | None:
        """尝试格式化日志记录为 "模块名: 消息内容" 格式。

        若 record.getMessage() 抛出 TypeError（格式字符串与参数不匹配），
        记录警告日志并返回 None。空消息也返回 None。

        Returns:
            格式化后的消息字符串，或 None（格式化失败 / 空消息）。
        """
        try:
            msg_content = record.getMessage()
        except TypeError:
            _logger.warning(
                "ChatUIErrorHandler: record.getMessage() 格式化失败 "
                "(args 不匹配格式字符串), record=%s", record.name,
            )
            return None
        if not msg_content:
            return None
        return f"{record.name}: {msg_content}"

    def emit(self, record: logging.LogRecord) -> None:
        """格式化 ERROR+ 日志记录并投递到 ChatUI 上屏。

        仅处理 ERROR/CRITICAL 级别，WARNING/INFO/DEBUG 跳过。
        空消息、空格式化结果、已被标记或线程重入中的 record 跳过。
        """
        # ★ 守卫条件串联：任一条件满足则跳过
        if self._is_below_error_level(record):
            return
        if self._is_reentrant():
            return
        if self._is_already_reported(record):
            return

        # ★ 格式化消息
        msg = self._try_format_message(record)
        if msg is None:
            return

        # ★ 截断超长消息
        msg = _truncate_msg(msg, self._max_length)

        # ★ 设置线程重入标记
        _handler_reentrant.is_active = True
        try:
            # ★ 延迟绑定：ChatUI 未激活时静默跳过
            consumer = _state.get_active_chat_ui()
            if consumer is not None:
                consumer.on_error(msg)
        finally:
            # ★ 无论 on_error 是否成功，都标记 record 已处理
            #   防止同 record 被多个 handler 或跨线程二次处理。
            #   即使 on_error 抛出异常（极罕见），也不再重试此 record。
            record._chatui_reported = True
            # ★ 清除线程重入标记
            _handler_reentrant.is_active = False

