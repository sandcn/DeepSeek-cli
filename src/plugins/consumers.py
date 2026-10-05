"""事件消费者插件 — 提供 ``ctx.consumers``。

「一切皆插件」：终端事件消费者（``OutputConsumer`` 输出事件直写、
``ChatUIConsumer`` 聊天界面渲染、ChatUI 日志错误处理器）作为**独立插件条目**
（``consumer``，经 ``src.tui.events.consumer_registry``）声明与注册——不再由本
服务硬编码构造，可被 Profile/Patch/Overlay 按 id 禁用、覆盖或替换：

- ``create_output_consumer(...)``：构造订阅内核 ``ctx.events`` 显示总线的
  ``OutputConsumer``；
- ``create_chat_ui()``：构造 ``ChatUIConsumer``；
- ``setup_error_handler()`` / ``teardown_error_handler()``：注册/注销日志
  错误上屏处理器（卸载时自动注销，可逆副作用）。

本聚合插件处理组合根注入的 ``managed_consumers`` 与 config
``disabled_consumers``。``plugins/app`` 经 ``ctx.consumers`` 装配输出消费者与
错误处理器（内核缺失时回退既有直接构造）。
"""

from __future__ import annotations

import logging

from ..kernel import Service, plugin
from ..tui.events.consumer_registry import (
    build_consumer,
    consumer_names,
    disable_builtin_consumers,
    set_managed_builtin_consumers,
)

_logger = logging.getLogger(__name__)


class ConsumersService(Service):
    """事件消费者服务 — 占据 ``ctx.consumers``。"""

    provide = "consumers"
    name = "consumers"
    inject = ("events",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_consumers") or ()
        if managed:
            undo_managed = set_managed_builtin_consumers(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_consumers") or ()
        if disabled:
            undo_disabled = disable_builtin_consumers(disabled)
            ctx.effect(lambda: undo_disabled)
        self._error_handler = None
        # 卸载时注销日志错误处理器（幂等；未注册时无副作用）
        ctx.effect(lambda: self.teardown_error_handler)

    def consumers(self) -> list:
        """当前生效的消费者 id 列表（自省）。"""
        return consumer_names()

    # ── 输出消费者 ───────────────────────────────────────

    def display_bus(self):
        """内核显示事件总线（消费者订阅目标）。"""
        return self.ctx.consume("events").display_bus

    def create_output_consumer(self, *, chat_ui_managed: bool = True, stream=None):
        """构造 OutputConsumer（经消费者注册表解析；缺席回退内置构造）。"""
        consumer = build_consumer(
            "output", event_bus=self.display_bus(), stream=stream,
            chat_ui_managed=chat_ui_managed,
        )
        if consumer is not None:
            return consumer
        from ..tui.events.consumers import OutputConsumer

        return OutputConsumer(
            event_bus=self.display_bus(),
            stream=stream,
            chat_ui_managed=chat_ui_managed,
        )

    # ── 聊天界面消费者 ───────────────────────────────────

    def create_chat_ui(self):
        """构造 ChatUIConsumer（经消费者注册表解析；缺席回退内置构造）。"""
        consumer = build_consumer("chat_ui")
        if consumer is not None:
            return consumer
        from ..tui.consumer import ChatUIConsumer

        return ChatUIConsumer()

    # ── 日志错误处理器 ───────────────────────────────────

    def setup_error_handler(self) -> None:
        """注册 ChatUI 日志错误上屏处理器（经消费者注册表；幂等）。"""
        handler = build_consumer("error_handler")
        if handler is None:
            from ..tui.consumer import setup_chat_ui_error_handler

            setup_chat_ui_error_handler()
            return
        start = getattr(handler, "start", None)
        if callable(start):
            start()
        self._error_handler = handler

    def teardown_error_handler(self) -> None:
        """注销 ChatUI 日志错误上屏处理器（幂等）。"""
        handler = self._error_handler
        self._error_handler = None
        if handler is not None:
            stop = getattr(handler, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    _logger.debug("注销 ChatUI 错误处理器异常", exc_info=True)
                return
        try:
            from ..tui.consumer import teardown_chat_ui_error_handler

            teardown_chat_ui_error_handler()
        except Exception:
            _logger.debug("注销 ChatUI 错误处理器异常", exc_info=True)


@plugin("consumers", inject=["events"], provide=["consumers"])
def apply(ctx):
    return ConsumersService(ctx, ctx.config)


__all__ = ["ConsumersService", "apply"]
