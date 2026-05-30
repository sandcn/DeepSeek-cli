"""审计日志中间件 — 记录模型调用和工具执行到审计日志"""

import logging

from ...config import audit_logger
from ..pipeline import AsyncMiddleware, PipelineContext

_logger = logging.getLogger(__name__)


class _AuditLogMiddleware(AsyncMiddleware):
    """审计日志中间件 — 记录模型调用和工具执行到审计日志"""

    def __init__(self):
        super().__init__()
        self._audit_logger = audit_logger

    @property
    def name(self) -> str:
        return "AuditLog"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        try:
            self._audit_logger.info(
                "model_call | model=%s | messages=%d | tools=%d",
                ctx.agent.model, len(ctx.agent.messages), len(ctx.agent.tools),
            )
        except Exception:
            _logger.exception("AuditLog.before_model_call 异常")

    async def after_tool_execution(self, ctx: PipelineContext) -> None:
        try:
            if ctx.tool_calls:
                tc_names = [tc.get("name", "?") for tc in ctx.tool_calls]
                self._audit_logger.info("tool_executed | %s", ", ".join(tc_names))
        except Exception:
            _logger.exception("AuditLog.after_tool_execution 异常")
