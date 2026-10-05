"""事件消费者插件 — 提供 ``ctx.consumers``。

「一切皆插件」：终端事件消费者（``OutputConsumer`` 输出事件直写、
``ChatUIConsumer`` 聊天界面渲染、ChatUI 日志错误处理器）不再由应用层直接
import 构造，而是经内核服务解析：

- ``create_output_consumer(...)``：构造订阅内核 ``ctx.events`` 显示总线的
  ``OutputConsumer``；
- ``create_chat_ui()``：构造 ``ChatUIConsumer``；
- ``setup_error_handler()`` / ``teardown_error_handler()``：注册/注销日志
  错误上屏处理器（卸载时自动注销，可逆副作用）。

可按 Profile/Patch 启用、禁用或替换；``plugins/app`` 经 ``ctx.consumers``
装配输出消费者与错误处理器（内核缺失时回退既有直接构造）。
"""

from __future__ import annotations

import logging

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class ConsumersService(Service):
    """事件消费者服务 — 占据 ``ctx.consumers``。"""

    provide = "consumers"
    name = "consumers"
    inject = ("events",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        # 卸载时注销日志错误处理器（幂等；未注册时无副作用）
        ctx.effect(lambda: self.teardown_error_handler)

    # ── 输出消费者 ───────────────────────────────────────

    def display_bus(self):
        """内核显示事件总线（消费者订阅目标）。"""
        return self.ctx.consume("events").display_bus

    def create_output_consumer(self, *, chat_ui_managed: bool = True, stream=None):
        """构造 OutputConsumer（订阅内核显示总线）。"""
        from ..tui.events.consumers import OutputConsumer

        return OutputConsumer(
            event_bus=self.display_bus(),
            stream=stream,
            chat_ui_managed=chat_ui_managed,
        )

    # ── 聊天界面消费者 ───────────────────────────────────

    def create_chat_ui(self):
        """构造 ChatUIConsumer（终端界面事件消费者）。"""
        from ..tui.consumer import ChatUIConsumer

        return ChatUIConsumer()

    # ── 日志错误处理器 ───────────────────────────────────

    def setup_error_handler(self) -> None:
        """注册 ChatUI 日志错误上屏处理器（幂等）。"""
        from ..tui.consumer import setup_chat_ui_error_handler

        setup_chat_ui_error_handler()

    def teardown_error_handler(self) -> None:
        """注销 ChatUI 日志错误上屏处理器（幂等）。"""
        try:
            from ..tui.consumer import teardown_chat_ui_error_handler

            teardown_chat_ui_error_handler()
        except Exception:
            _logger.debug("注销 ChatUI 错误处理器异常", exc_info=True)


@plugin("consumers", inject=["events"], provide=["consumers"])
def apply(ctx):
    return ConsumersService(ctx)


__all__ = ["ConsumersService", "apply"]
