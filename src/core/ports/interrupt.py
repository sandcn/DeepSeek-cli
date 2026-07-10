"""中断检查端口 — 核心层与中断信号基础设施的接口"""
from __future__ import annotations
from abc import ABC, abstractmethod


class InterruptPort(ABC):
    """抽象中断检查端口

    核心层通过此接口检查是否收到中断信号、请求中断、重置中断。
    基础设施层（api/interrupt_async）实现此接口以提供具体中断检查能力。
    """

    @abstractmethod
    async def is_interrupted(self) -> bool:
        """检查是否已请求中断。"""
        ...

    @abstractmethod
    def request_interrupt(self) -> None:
        """请求中断。"""
        ...

    @abstractmethod
    def reset_interrupt(self) -> None:
        """重置中断信号。"""
        ...


class DefaultInterruptAdapter(InterruptPort):
    """默认中断适配器 — 包装 src/api/interrupt_async"""

    async def is_interrupted(self) -> bool:
        from ...api.interrupt_async import is_interrupted_async
        return await is_interrupted_async()

    def request_interrupt(self) -> None:
        from ...api.interrupt_async import request_interrupt_async
        request_interrupt_async()

    def reset_interrupt(self) -> None:
        from ...api.interrupt_async import reset_interrupt_async
        reset_interrupt_async()
