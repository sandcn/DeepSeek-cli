"""中断检查中间件 — 在每次模型调用前检查中断信号"""

from ..pipeline import AsyncMiddleware, PipelineContext


class _InterruptCheckMiddleware(AsyncMiddleware):
    """中断检查中间件 — 在每次模型调用前检查中断信号"""

    def __init__(self, is_interrupted_check=None):
        self._is_interrupted_check = is_interrupted_check

    @property
    def name(self) -> str:
        return "InterruptCheck"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        if self._is_interrupted_check is None:
            from ...api.interrupt_async import is_interrupted_async
            check_fn = is_interrupted_async
        else:
            check_fn = self._is_interrupted_check
        if await check_fn():
            ctx.interrupted = True
