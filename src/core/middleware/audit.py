"""审计日志中间件 — 记录模型调用和工具执行到审计日志"""

import logging

from ..pipeline import AsyncMiddleware, PipelineContext

_logger = logging.getLogger(__name__)

# 审计日志记录器（模块级，延迟加载）
# 支持测试通过 mock.patch("src.core.middleware.audit.audit_logger") 进行断言
audit_logger = None  # type: ignore


def _get_audit_logger():
    """获取审计日志记录器（延迟加载）"""
    global audit_logger
    if audit_logger is None:
        from ...config import audit_logger as _al
        audit_logger = _al
    return audit_logger


class _AuditLogMiddleware(AsyncMiddleware):
    """审计日志中间件 — 记录模型调用和工具执行到审计日志"""

    @property
    def name(self) -> str:
        return "AuditLog"

    async def before_model_call(self, ctx: PipelineContext) -> None:
        try:
            logger = _get_audit_logger()
            logger.info(
                "model_call | model=%s | messages=%d | tools=%d",
                ctx.agent.model, len(ctx.agent.messages), len(ctx.agent.tools),
            )
        except Exception:
            _logger.exception("AuditLog.before_model_call 异常")

    async def after_tool_execution(self, ctx: PipelineContext) -> None:
        try:
            if ctx.tool_calls:
                tc_names = [tc.get("name", "?") for tc in ctx.tool_calls]
                logger = _get_audit_logger()
                logger.info("tool_executed | %s", ", ".join(tc_names))
        except Exception:
            _logger.exception("AuditLog.after_tool_execution 异常")
