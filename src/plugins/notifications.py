"""通知插件 — 提供 ``ctx.notifications``。

「一切皆插件」：桌面通知（Termux / Linux notify-send / Windows Toast）
作为可替换的 provider 坐在内核之上。默认 provider 转发 ``src.notifications``
的平台实现；外部插件可经 ``ctx.notifications.set_provider(...)`` 替换为
自定义通知后端（如 Webhook、企业 IM），或替换为 Null provider 完全关闭。

应用层（``app_loop/_session_setup``）经本服务发送「对话完成」通知，
不再直接 import 通知实现。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class _DefaultNotificationsProvider:
    """默认通知 provider — 转发 ``src.notifications`` 平台实现。"""

    def notify_chat_completed(self, messages: list, elapsed: float | None = None) -> None:
        from ..notifications import notify_chat_completed

        notify_chat_completed(messages, elapsed=elapsed)

    async def async_notify_chat_completed(self, messages: list, elapsed: float | None = None) -> None:
        from ..notifications import async_notify_chat_completed

        await async_notify_chat_completed(messages, elapsed=elapsed)


class NotificationsService(Service):
    """通知服务 — 占据 ``ctx.notifications``。"""

    provide = "notifications"
    name = "notifications"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_notification_backends") or ()
        if managed:
            from ..notifications.registry import set_managed_builtin_notification_backends

            undo_managed = set_managed_builtin_notification_backends(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_notification_backends") or ()
        if disabled:
            from ..notifications.registry import disable_builtin_notification_backends

            undo_disabled = disable_builtin_notification_backends(disabled)
            ctx.effect(lambda: undo_disabled)
        self._provider: Any = _DefaultNotificationsProvider()
        ctx.effect(lambda: self._on_unload)

    def _on_unload(self) -> None:
        self._provider = None

    def backends(self) -> list:
        """当前生效的通知后端 id 列表（自省）。"""
        from ..notifications.registry import (
            builtin_notification_backend_factories,
            notification_backend_factories,
        )

        return sorted(set(builtin_notification_backend_factories()) | set(notification_backend_factories()))

    @property
    def provider(self):
        return self._provider

    def port(self):
        return self._provider

    def set_provider(self, provider) -> Any:
        previous = self._provider
        self._provider = provider
        return previous

    def notify_chat_completed(self, messages: list, elapsed: float | None = None) -> None:
        """发送「对话完成」通知（provider 缺失时静默跳过）。"""
        provider = self._provider
        if provider is None:
            return
        try:
            provider.notify_chat_completed(messages, elapsed=elapsed)
        except Exception:
            _logger.warning("通知发送失败（不阻断会话）", exc_info=True)

    async def async_notify_chat_completed(self, messages: list, elapsed: float | None = None) -> None:
        provider = self._provider
        if provider is None:
            return
        method = getattr(provider, "async_notify_chat_completed", None)
        if not callable(method):
            self.notify_chat_completed(messages, elapsed=elapsed)
            return
        try:
            await method(messages, elapsed=elapsed)
        except Exception:
            _logger.warning("异步通知发送失败（不阻断会话）", exc_info=True)


@plugin("notifications", provide=["notifications"])
def apply(ctx):
    return NotificationsService(ctx, ctx.config)


__all__ = ["NotificationsService", "apply"]
