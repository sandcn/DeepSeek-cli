"""中断检查端口适配器 — DefaultInterruptAdapter、MockInterruptAdapter"""
from __future__ import annotations

from ..ports.interrupt import InterruptPort


class DefaultInterruptAdapter(InterruptPort):
    """默认中断检查适配器 — 包装 src/api/interrupt_async

    使用延迟导入（方法体内导入）避免在模块加载时引入 api 基础设施层的副作用。
    """

    async def is_interrupted(self) -> bool:
        from ...api.interrupt_async import is_interrupted_async
        return await is_interrupted_async()

    async def reset(self) -> None:
        from ...api.interrupt_async import reset_interrupt_async
        reset_interrupt_async()

    async def request_interrupt(self) -> None:
        from ...api.interrupt_async import request_interrupt_async
        request_interrupt_async()


class MockInterruptAdapter(InterruptPort):
    """Mock 中断检查适配器 — 用于测试

    默认未中断，可通过 set_interrupted() 控制中断状态。
    """

    def __init__(self):
        self._interrupted = False
        self.reset_called = False
        self.request_called = False

    async def is_interrupted(self) -> bool:
        return self._interrupted

    async def reset(self) -> None:
        self._interrupted = False
        self.reset_called = True

    async def request_interrupt(self) -> None:
        self._interrupted = True
        self.request_called = True

    def set_interrupted(self, value: bool = True) -> None:
        """直接设置中断状态（非异步，用于同步测试环境）"""
        self._interrupted = value
