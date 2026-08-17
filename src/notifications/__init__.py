"""桌面通知模块 — 封装多平台通知逻辑

支持 Termux、Linux notify-send、Windows Toast 三种通知方式。
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time


import shutil

from ..config import get_rc

from ..tools.utils import async_termux_notify, termux_notify, get_last_user_message_preview

_logger = logging.getLogger(__name__)

# -- 内部状态 --------------------------------------------
_last_notify_time: float = 0.0
_COOLDOWN_SECONDS = 30
_HAS_NOTIFY_SEND: bool | None = None


# -- 公开函数 --------------------------------------------

def _format_notify_title(elapsed: float | None = None) -> str:
    """格式化通知标题，含耗时信息。"""
    if elapsed is None:
        return "聊天完成"
    if elapsed < 60:
        return f"聊天完成 耗时{elapsed:.1f}秒"
    elif elapsed < 3600:
        minutes = int(elapsed // 60)
        secs = int(elapsed % 60)
        return f"聊天完成 耗时{minutes}分{secs}秒"
    else:
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        return f"聊天完成 耗时{hours}时{minutes}分"


def _build_notification(messages: list[dict], elapsed: float | None = None) -> tuple[str, str] | None:
    """构建通知预览和标题，含冷却检查和预览提取。

    Returns:
        (preview, title) 或 None（应跳过通知）
    """
    preview = _prepare_notify(messages)
    if preview is None:
        return None
    return (preview, _format_notify_title(elapsed))


def notify_chat_completed(messages: list[dict], elapsed: float | None = None) -> None:
    """对话完成时发送桌面通知（同步/异步非阻塞）

    带 30 秒冷却：同一进程内连续重复通知会被静默跳过。

    Args:
        messages: 当前对话消息列表
        elapsed: 本轮对话耗时（秒），用于在标题中显示耗时
    """
    result = _build_notification(messages, elapsed)
    if result is None:
        return
    preview, title = result

    # Termux 通知（纯同步，不依赖事件循环）
    termux_notify(
        message=preview,
        title=title,
        vibrate=True, duration=10000, notification=True,
        sound=True, toast=False,
    )

    # Linux / Windows 需要事件循环
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as e:
        _logger.debug("跳过通知（无事件循环）: %s", e)
        return

    _notify_linux(loop, preview, title)
    if sys.platform == "win32":
        _notify_windows(loop, preview, title)


async def async_notify_chat_completed(messages: list[dict], elapsed: float | None = None) -> None:
    """对话完成时发送桌面通知（异步协程版本）

    带 30 秒冷却：同一进程内连续重复通知会被静默跳过。

    Args:
        messages: 当前对话消息列表
        elapsed: 本轮对话耗时（秒），用于在标题中显示耗时
    """
    result = _build_notification(messages, elapsed)
    if result is None:
        return
    preview, title = result

    # Termux 通知（异步）
    await async_termux_notify(
        message=preview,
        title=title,
        vibrate=True, duration=10000, notification=True,
        sound=True, toast=False,
    )

    # Linux notify-send + Windows Toast 的 Future，用于收集异常
    pending_futures: list[asyncio.Future] = []
    loop = asyncio.get_running_loop()

    pending_futures.append(loop.run_in_executor(None, _run_notify_send, preview, title))

    if sys.platform == "win32":
        pending_futures.append(loop.run_in_executor(None, _run_windows_toast, preview, title))

    try:
        # 等待通知完成，但不阻塞其他异步任务
        for fut in pending_futures:
            try:
                await asyncio.wrap_future(fut)
            except Exception as e:
                _logger.debug("通知执行失败: %s", e)
    finally:
        for fut in pending_futures:
            if not fut.done():
                fut.cancel()


# -- 内部函数 --------------------------------------------

def _prepare_notify(messages: list[dict]) -> str | None:
    """检查冷却并生成 preview，返回 preview 或 None（跳过）。"""
    global _last_notify_time
    now = time.monotonic()
    if now - _last_notify_time < _COOLDOWN_SECONDS:
        return None
    preview = get_last_user_message_preview(messages) or "AI 已回复"
    if not (get_rc().get("enable_notifications", True) and get_rc().get("notify_on_chat_completion", True)):
        return None
    _last_notify_time = now
    return preview


def _check_notify_send() -> bool:
    """检查 notify-send 是否可用（结果缓存）。"""
    global _HAS_NOTIFY_SEND
    if _HAS_NOTIFY_SEND is None:
        _HAS_NOTIFY_SEND = shutil.which("notify-send") is not None
    return _HAS_NOTIFY_SEND


def _notify_linux(loop: asyncio.AbstractEventLoop, preview: str, title: str = "聊天完成") -> None:
    """Linux notify-send 通知"""
    if not _check_notify_send():
        return
    loop.run_in_executor(None, _run_notify_send, preview, title)


def _notify_windows(loop: asyncio.AbstractEventLoop, preview: str, title: str = "聊天完成") -> None:
    """Windows PowerShell Toast 通知"""
    loop.run_in_executor(None, _run_windows_toast, preview, title)


def _run_notify_send(preview: str, title: str = "聊天完成") -> None:
    """子线程执行 notify-send"""
    import subprocess
    try:
        subprocess.run(
            ["notify-send", title, preview, "-t", "10000"],
            capture_output=True, timeout=3,
        )
    except Exception as e:
        _logger.debug("notify-send 失败: %s", e)


def _run_windows_toast(preview: str, title: str = "聊天完成") -> None:
    """子线程执行 PowerShell Toast（通过环境变量传参，避免注入）"""
    import subprocess
    import os
    try:
        # 通过环境变量传递参数，避免 PowerShell 命令注入
        env = os.environ.copy()
        env['_CHAT_TOAST_TITLE'] = title
        env['_CHAT_TOAST_MSG'] = preview
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
