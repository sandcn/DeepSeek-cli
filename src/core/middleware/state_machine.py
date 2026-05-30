"""状态机 Pipeline 中间件 — 自动触发会话状态转换

在 Pipeline 完成一轮对话后，根据结果自动触发
SessionStateMachine 的状态转换（RUNNING → COMPLETED / INTERRUPTED）。

使用方式:
    pipeline = Pipeline()
    pipeline.use_async(StateMachineMiddleware())
    
    # 在 session._execute_round() 中通过 PipelineContext 携带状态机引用:
    # ctx.session_state_machine = state_machine
    await pipeline.run_round_async(ctx)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..pipeline import AsyncMiddleware, PipelineContext

if TYPE_CHECKING:
    from ..state_machine import SessionStateMachine

_logger = logging.getLogger(__name__)


class StateMachineMiddleware(AsyncMiddleware):
    """状态机中间件 — Pipeline 完成时自动触发状态转换

    通过 ctx.session_state_machine 获取状态机引用。
    仅在引用存在时生效（由 PipelineContext 携带），
    无引用时静默跳过（适用于测试或无需状态管理的场景）。
    """

    @property
    def name(self) -> str:
        return "StateMachine"

    async def on_round_complete(self, ctx: PipelineContext) -> None:
        """Pipeline 一轮对话完成时触发状态转换

        根据 ctx.interrupted 执行：
        - True  → state_machine.interrupt()    (RUNNING → INTERRUPTED)
        - False → state_machine.complete_round() (RUNNING → COMPLETED)
        """
        sm: SessionStateMachine | None = getattr(ctx, 'session_state_machine', None)
        if sm is None:
            return

        from ..state_machine import SessionState, InvalidTransitionError

        # 仅在当前状态为 RUNNING 时才转换（防止重复转换）
        if not sm.is_(SessionState.RUNNING):
            return

        try:
            if ctx.interrupted:
                sm.interrupt()
                _logger.debug("状态机: RUNNING → INTERRUPTED (by middleware)")
            else:
                sm.complete_round()
                _logger.debug("状态机: RUNNING → COMPLETED (by middleware)")
        except InvalidTransitionError:
            _logger.warning(
                "StateMachineMiddleware 状态转换失败: %s → %s",
                sm.name,
                "interrupt" if ctx.interrupted else "complete",
            )
        except Exception as exc:
            _logger.exception("StateMachineMiddleware 异常: %s", exc)
