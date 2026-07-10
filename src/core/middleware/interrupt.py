"""中断检查中间件 — 在每次模型调用前检查中断信号"""

from ..pipeline import AsyncMiddleware, PipelineContext


class _InterruptCheckMiddleware(AsyncMiddleware):
    """中断检查中间件 — 在每次模型调用前检查中断信号"""

    def __init__(self, interrupt_port=None):
        if interrupt_port is None:
            from ...core.ports.interrupt import DefaultInterruptAdapter
            self._interrupt_port = DefaultInterruptAdapter()
        else:
            self._interrupt_port = interrupt_port

    @property
    def name(self) -> str:
        return "InterruptCheck"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        if await self._interrupt_port.is_interrupted():
            ctx.interrupted = True
