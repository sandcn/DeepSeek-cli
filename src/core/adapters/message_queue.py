"""MessageQueue 适配器 — NullMessageQueue（测试用空实现）

提供 MessageQueuePort 的空实现（NullMessageQueue），
适用于测试场景——所有操作无实际效果，不阻塞、不排队。
"""
from __future__ import annotations

from typing import Callable, Awaitable, Optional

from ..ports.message_queue import MessageQueuePort, Message


class NullMessageQueue(MessageQueuePort):
    """MessageQueuePort 的空实现 — 所有操作无效果，适用于测试。

    - put(content)    → 始终成功，返回 Message(content=content)
    - get(timeout)    → 始终返回 None（无消息）
    - stop()          → pass
    - is_running      → 始终返回 False
    - qsize           → 始终返回 0
    - async_consume() → 直接返回（不阻塞）
    """

    async def put(self, content: str | object) -> Message:
        """始终成功，返回 Message(content=content)。"""
        return Message(content=content)

    async def get(self, timeout: Optional[float] = None) -> Optional[Message]:
        """始终返回 None（无消息）。"""
        return None

    async def stop(self) -> None:
        """空操作。"""
        pass

    @property
    def is_running(self) -> bool:
        """始终返回 False。"""
        return False

    @property
    def qsize(self) -> int:
        """始终返回 0。"""
        return 0

    async def async_consume(
        self,
        callback: Callable[["Message"], Awaitable[None]],
        poll_interval: float = 0.5,
    ) -> None:
        """直接返回，不阻塞。

        注意：poll_interval 参数在此实现中忽略，该方法始终立即返回。
        """
        pass
