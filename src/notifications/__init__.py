"""桌面通知模块 — 按生效后端扇出多平台通知。

支持 Termux、Linux notify-send、Windows Toast 三种通知方式。

「一切皆插件」：具体平台发送逻辑拆为独立通知后端
（``src.notifications.backends``），由注册表（``src.notifications.registry``）
装配；每个后端是清单中的独立插件条目（``notification_backend``），可被
Profile/Patch/Overlay 禁用或替换。本模块只保留标题/预览构建与 30 秒冷却，
并按**生效后端**扇出发送。
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..config import get_rc
from ..tools.utils import get_last_user_message_preview

_logger = logging.getLogger(__name__)

# -- 内部状态 --------------------------------------------
_last_notify_time: float = 0.0
_COOLDOWN_SECONDS = 30


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


def notify_chat_completed(messages: list[dict], elapsed: float | None = None) -> None:
    """对话完成时发送桌面通知（同步/非阻塞）。

    带 30 秒冷却：同一进程内连续重复通知会被静默跳过。
    按注册表的**生效后端**依次发送；单个后端异常只记日志，不影响其他后端。
    """
    result = _build_notification(messages, elapsed)
    if result is None:
        return
    preview, title = result

    from .registry import active_notification_backends

    for backend in active_notification_backends():
        try:
            backend.send(preview, title)
        except Exception:
            _logger.debug("通知后端发送失败: %s", type(backend).__name__, exc_info=True)


async def async_notify_chat_completed(messages: list[dict], elapsed: float | None = None) -> None:
    """对话完成时发送桌面通知（异步协程版本）。

    带 30 秒冷却：同一进程内连续重复通知会被静默跳过。
    """
    result = _build_notification(messages, elapsed)
    if result is None:
        return
    preview, title = result

    from .registry import active_notification_backends

    backends = active_notification_backends()
    if not backends:
        return

    async def _send(backend) -> None:
        try:
            asend = getattr(backend, "asend", None)
            if callable(asend):
                await asend(preview, title)
            else:
                backend.send(preview, title)
        except Exception:
            _logger.debug("通知后端异步发送失败: %s", type(backend).__name__, exc_info=True)

    await asyncio.gather(*(_send(backend) for backend in backends), return_exceptions=True)


__all__ = [
    "notify_chat_completed",
    "async_notify_chat_completed",
]
