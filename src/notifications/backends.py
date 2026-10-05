"""内置通知后端实现 — Termux / Linux notify-send / Windows Toast 三种平台。

每个后端实现 ``send(preview, title)``（同步）与可选 ``asend(preview, title)``
（异步）。它们由 ``src.notifications.registry`` 注册、由
``src.notifications.notify_chat_completed`` 按生效后端扇出调用；也可作为独立
插件条目（``notification_backend``）被 Profile/Patch/Overlay 禁用或替换。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys

_logger = logging.getLogger(__name__)

#: notify-send 可用性缓存（Linux 后端）
_HAS_NOTIFY_SEND: bool | None = None


def _check_notify_send() -> bool:
    global _HAS_NOTIFY_SEND
    if _HAS_NOTIFY_SEND is None:
        _HAS_NOTIFY_SEND = shutil.which("notify-send") is not None
    return _HAS_NOTIFY_SEND


class NotificationBackend:
    """通知后端基类。"""

    id = ""

    def send(self, preview: str, title: str) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    async def asend(self, preview: str, title: str) -> None:
        """默认异步实现：在线程池执行同步 send（不阻塞事件循环）。"""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.send, preview, title)


class TermuxBackend(NotificationBackend):
    """Termux 通知后端。"""

    id = "termux"

    def send(self, preview: str, title: str) -> None:
        from ..tools.utils import termux_notify

        termux_notify(
            message=preview, title=title, vibrate=True, duration=10000,
            notification=True, sound=True, toast=False,
        )

    async def asend(self, preview: str, title: str) -> None:
        from ..tools.utils import async_termux_notify

        await async_termux_notify(
            message=preview, title=title, vibrate=True, duration=10000,
            notification=True, sound=True, toast=False,
        )


class LinuxBackend(NotificationBackend):
    """Linux notify-send 后端。"""

    id = "linux"

    def send(self, preview: str, title: str) -> None:
        if not _check_notify_send():
            return
        import subprocess

        try:
            subprocess.run(
                ["notify-send", title, preview, "-t", "10000"],
                capture_output=True, timeout=3,
            )
        except Exception as e:
            _logger.debug("notify-send 失败: %s", e)


class WindowsBackend(NotificationBackend):
    """Windows PowerShell Toast 后端。"""

    id = "windows"

    def send(self, preview: str, title: str) -> None:
        import subprocess

        try:
            env = os.environ.copy()
            env["_CHAT_TOAST_TITLE"] = title
            env["_CHAT_TOAST_MSG"] = preview
            ps_script = '''
$title = $env:_CHAT_TOAST_TITLE
$msg = $env:_CHAT_TOAST_MSG
$appId = "Chat"
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $x = $t.GetElementsByTagName("text")
    $x.Item(0).AppendChild($t.CreateTextNode($title)) | Out-Null
    $x.Item(1).AppendChild($t.CreateTextNode($msg)) | Out-Null
    $n = [Windows.UI.Notifications.ToastNotification]::new($t)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($n)
} catch {}
'''
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, timeout=10, env=env,
            )
        except Exception as e:
            _logger.debug("PowerShell Toast 失败: %s", e)


__all__ = ["NotificationBackend", "TermuxBackend", "LinuxBackend", "WindowsBackend"]
