"""MessageQueue 端口 — 核心层异步消息队列抽象接口

定义消息队列的抽象协议（MessageQueuePort），核心层通过此接口
进行消息的生产与消费。适配器层提供具体实现
（如 AsyncMessageQueue、NullMessageQueue），
实现依赖倒置（核心层 → 抽象 ← 适配器层）。
"""
from __future__ import annotations

import itertools
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

# 全局消息 ID 生成器（线程安全无需锁，全在事件循环中）
_message_counter = itertools.count(1)


@dataclass
class Message:
    """队列中的消息单元"""
    content: str | object
    timestamp: float = field(default_factory=time.time)
    id: int = field(default_factory=lambda: next(_message_counter))
    taken: bool = False  # 标记消息是否已被 callback 取出处理（双阶段确认防重复）


# 哨兵标记值，用于停止消费循环
_STOP_SENTINEL = object()


class MessageQueuePort(ABC):
    """异步消息队列端口 — 定义消息队列的抽象协议。

    核心层通过此接口进行消息的生产与消费，不依赖具体实现。
    适配器层提供具体实现（如 AsyncMessageQueue、NullMessageQueue）。

    方法清单:
    - put(content)      — 生产者：放入消息
    - get(timeout)      — 消费者：取出消息
    - stop()            — 停止队列
    - is_running        — 队列运行状态
    - qsize             — 待处理消息数
    - async_consume()   — 异步消费循环
    """

    @abstractmethod
    async def put(self, content: str | object) -> Message:
        """生产者：将消息放入队列，返回 Message 对象。

        Args:
            content: 消息内容（字符串或其他对象）

        Returns:
            创建的 Message 实例
        """
        ...

    @abstractmethod
    async def get(self, timeout: Optional[float] = None) -> Optional[Message]:
        """消费者：从队列取出消息。

        Args:
            timeout: 超时秒数
                - None（默认）：阻塞直到有消息
                - 0：非阻塞，无消息立即返回 None
                - >0：最多等待 timeout 秒

        Returns:
            Message 对象，超时/无消息时返回 None
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止队列：放入哨兵消息，消费循环收到后自动退出。"""
        ...

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """队列是否在运行"""
        ...

    @property
    @abstractmethod
    def qsize(self) -> int:
        """队列中待处理的消息数"""
        ...

    @abstractmethod
    async def async_consume(
        self,
        callback: Callable[["Message"], Awaitable[None]],
        poll_interval: float = 0.5,
    ) -> None:
        """异步消费循环：持续取消息并调用异步 callback。

        Args:
            callback: 异步回调，接收 Message 对象
            poll_interval: get 内部超时秒数，控制轮询频率
        """
        ...
