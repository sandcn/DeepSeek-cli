"""中断检查中间件 — 在每次模型调用前检查中断信号"""

from __future__ import annotations

from typing import Optional

from ..ports.interrupt import InterruptPort
from ..adapters.interrupt import DefaultInterruptAdapter
from ..pipeline import AsyncMiddleware, PipelineContext


class _InterruptCheckMiddleware(AsyncMiddleware):
    """中断检查中间件 — 在每次模型调用前检查中断信号

    构造时注入 InterruptPort，不再直接依赖 api/interrupt_async。
    不传参数时使用 DefaultInterruptAdapter 保持向后兼容。
    """

    def __init__(self, interrupt_port: Optional[InterruptPort] = None):
        self._interrupt_port = interrupt_port or DefaultInterruptAdapter()

    @property
    def name(self) -> str:
        return "InterruptCheck"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        if await self._interrupt_port.is_interrupted():
            ctx.interrupted = True
