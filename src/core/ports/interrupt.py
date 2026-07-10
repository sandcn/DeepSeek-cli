"""中断检查端口 — 核心层与中断信号基础设施的接口"""
from __future__ import annotations
from abc import ABC, abstractmethod


class InterruptPort(ABC):
    """抽象中断检查端口

    核心层通过此接口检查是否收到中断信号。
    基础设施层（api/interrupt_async）实现此接口以提供具体中断检查能力。
    """

    @abstractmethod
    async def is_interrupted(self) -> bool:
        """检查是否已请求中断。

        Returns:
            True 表示已收到中断信号，应停止当前操作
        """
        ...

    @abstractmethod
    async def reset(self) -> None:
        """清除中断信号。"""
        ...

    @abstractmethod
    async def request_interrupt(self) -> None:
        """请求中断。"""
        ...
