"""
MessageQueue — 异步消息队列

生产者（用户输入）将消息放入队列，
消费者（大模型 Agent）从队列取出消息并处理。

解耦了输入获取和模型处理，支持消息排队。

基于 asyncio.Queue 实现，所有操作均为异步，
无需 run_in_executor 桥接，适用于纯 asyncio 环境。
"""
from __future__ import annotations

import asyncio
import logging
import time
import itertools
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable

# 模块级日志器
_logger = logging.getLogger(__name__)

# 全局消息 ID 生成器（线程安全无需锁，全在事件循环中）
_message_counter = itertools.count(1)


@dataclass
class Message:
    """队列中的消息单元"""
    content: str | object
    timestamp: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_message_counter))
    taken: bool = False  # 标记消息是否已被 callback 取出处理（双阶段确认防重复）


class MessageQueue:
    """基于 asyncio.Queue 的 FIFO 消息队列（纯异步实现）。

    所有 put / get / stop 操作均为 async，需在事件循环中调用。
    """

    _STOP_SENTINEL = object()  # 哨兵标记值，用于停止消费循环

    def __init__(self):
        self._queue: asyncio.Queue[Message] = asyncio.Queue()
        self._running = True

    async def put(self, content: str | object) -> Message:
        """生产者：将消息放入队列，返回 Message 对象。

        异步非阻塞（asyncio.Queue 无容量上限时直接入队）。
        """
        msg = Message(content=content)
        await self._queue.put(msg)
        return msg

    async def get(self, timeout: Optional[float] = None) -> Optional[Message]:
        """消费者：从队列取出消息。

        timeout 说明：
          - None（默认）：阻塞直到有消息
          - 0：非阻塞，无消息立即返回 None
          - >0：最多等待 timeout 秒

        返回 Message 或 None（超时/非阻塞无消息时）。
        """
        try:
            if timeout == 0:
                # 非阻塞
                return self._queue.get_nowait()
            elif timeout is None:
                # 无限等待
                return await self._queue.get()
            else:
                # 有限超时等待
                return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.QueueEmpty):
            return None

    async def stop(self):
        """停止队列：放入哨兵消息，消费循环收到后自动退出。"""
        await self._queue.put(Message(content=self._STOP_SENTINEL))
        self._running = False

    @property
    def is_running(self) -> bool:
        """队列是否在运行"""
        return self._running

    @property
    def qsize(self) -> int:
        """队列中待处理的消息数"""
        return self._queue.qsize()

    # ═══════════════════════════════════════════════════════════
    # 异步消费
    # ═══════════════════════════════════════════════════════════

    async def async_consume(
        self,
        callback: Callable[["Message"], Awaitable[None]],
        poll_interval: float = 0.5,
    ) -> None:
        """异步消费循环：持续取消息并调用异步 callback。

        通过 asyncio.Queue.get() 异步等待，不阻塞事件循环。

        停止方式：
        - 调用 stop() 放入哨兵消息
        - 直接取消此协程（asyncio.CancelledError）

        Args:
            callback: 异步回调，接收 Message 对象
            poll_interval: get 内部超时秒数，控制轮询频率
        """
        try:
            while self._running:
                msg = await self.get(timeout=poll_interval)
                if msg is None:
                    continue
                if msg.content is self._STOP_SENTINEL:
                    break
                # 双阶段确认：先标记消息已取出（taken=True），再交给 callback
                # taken 必须放在 try 块外，确保标记不因 CancelledError 回滚丢失
                msg.taken = True
                try:
                    await callback(msg)
                except asyncio.CancelledError:
                    if msg.taken:
                        # callback 已开始处理消息，跳过重新入队避免重复处理
                        _logger.warning("async_consume 取消时 msg.taken=True，跳过重新入队（防重复）")
                        self._running = False
                    else:
                        # callback 尚未开始处理，将消息重新入队避免丢失
                        _logger.warning("async_consume 取消时 msg.taken=False，重新入队消息 id=%d", msg.id)
                        self._running = False
                        await self._queue.put(msg)
                    raise
                except Exception as e:
                    _logger.exception("异步消费者回调异常: %s", e)
        except asyncio.CancelledError:
            _logger.info("async_consume 被取消")
            raise
