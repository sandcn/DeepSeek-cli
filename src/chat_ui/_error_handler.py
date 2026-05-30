"""ChatUIErrorHandler — 将 ERROR+ 级别日志投递到 ChatUI 上屏。

模块级自动注册到 root logger（通过模块级代码），
emit() 中格式化 log record 并调用 get_active_chat_ui().on_error()。
设计为纯入队操作（不 I/O），线程安全。

注意：get_active_chat_ui 通过 sys.modules 动态查找，而非模块级 import。
这样当测试用 patch.object(chat_ui, 'get_active_chat_ui', ...) 时，
emit() 能正确获取到被 patch 的版本。
"""

from __future__ import annotations

import logging
import sys
import threading

# ── 线程本地重入保护（防止 emit → logger → emit 递归） ──
_handler_reentrant = threading.local()


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

    def __init__(self, max_length: int = 200):
        super().__init__(level=logging.ERROR)
        self._max_length = max_length

    @staticmethod
    def _get_consumer():
        """通过 sys.modules 动态查找 get_active_chat_ui。

        使用模块动态查找而非直接 import，使测试中的
        patch.object(chat_ui, 'get_active_chat_ui', ...) 能够生效——
        patch 修改的是模块的属性字典，通过 sys.modules 访问
        能拿到被 patch 后的版本。
        """
        mod = sys.modules.get('src.chat_ui')
        if mod is None:
            return None
        getter = getattr(mod, 'get_active_chat_ui', None)
        return getter() if getter else None

    def emit(self, record: logging.LogRecord) -> None:
        """格式化 ERROR+ 日志记录并投递到 ChatUI 上屏。

        仅处理 ERROR/CRITICAL 级别，WARNING/INFO/DEBUG 跳过。
        空消息、空格式化结果、已被标记或线程重入中的 record 跳过。
        """
        # ★ 只处理 ERROR+ 级别（防御纵深：即使绕过 super().__init__(level=...)
        #   直接调用 emit()，此处也能保证正确过滤）
        if record.levelno < logging.ERROR:
            return

        # ★ 防自引用循环：线程重入检测（同线程 emit 递归阻断）
        if getattr(_handler_reentrant, 'is_active', False):
            return

        # ★ 防自引用循环：已标记的 record 跳过（跨调用/跨 handler 保护）
        if getattr(record, '_chatui_reported', False):
            return

        # ★ 格式化消息（格式: "模块名: 消息内容"）
        msg_content = record.getMessage()
        if not msg_content:
            return
        msg = f"{record.name}: {msg_content}"

        # ★ 截断超长消息
        if len(msg) > self._max_length:
            msg = msg[:self._max_length] + "..."

        # ★ 设置线程重入标记
        _handler_reentrant.is_active = True
        try:
            # ★ 延迟绑定：ChatUI 未激活时静默跳过
            consumer = self._get_consumer()
            if consumer is not None:
                consumer.on_error(msg)
            # ★ on_error 成功后设置 record 标记，防止同 record 被多个 handler
            #   或跨线程二次处理。若 on_error 抛出异常，标记不会误设——错误信
            #   息会被 _drain_queue 的 try/except 记录到日志（有 _chatui_reported
            #   保护不会递归），后续同 record 的 emit 不会被跳过。
            record._chatui_reported = True
        finally:
            # ★ 清除线程重入标记
            _handler_reentrant.is_active = False


# ── 注册到 root logger（模块级，全局生效） ────────────
_error_handler = ChatUIErrorHandler()
logging.getLogger().addHandler(_error_handler)
