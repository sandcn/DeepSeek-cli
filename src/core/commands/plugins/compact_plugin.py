"""CompactPlugin — 压缩上下文历史（``/compact``）。

对齐 dsh ``dsh-command-compact``：无参数地把最旧的可压缩范围替换为一条
结构化检查点（保留近期尾部），并报告压缩条数与节省 token；无可安全压缩
范围时报告「暂无可压缩的历史」。压缩过程经显示事件在 TUI 呈现。

异步路径经 ``asyncio.to_thread`` 执行（压缩内部是阻塞模型调用），不阻塞
渲染线程；同步路径（非 TUI 命令调用）同样可用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, declare_command_plugin

_logger = logging.getLogger(__name__)

#: 手动压缩预期失败的分类提示（对应 ManualCompactionErrorCode）。
_FAILURE_TEXT = {
    "busy": "压缩暂不可用：正有压缩进行中，或会话不在空闲状态",
    "cancelled": "压缩已取消",
    "changed": "待压缩的历史在替换前发生了变化（尝试已记录到会话日志）",
    "summary": "压缩未能产出有用的摘要（尝试已记录到会话日志）",
    "commit": "压缩未干净完成，部分历史可能已变更，请检查当前会话状态",
    "persistence": "压缩已完成，但会话未能保存",
}


class CompactPlugin(InteractiveCommandPlugin):
    """压缩上下文历史（``/compact``）。"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="compact",
            description="压缩上下文历史（结构化检查点）",
            group="session",
            usage="",
        )

    # ── 执行入口 ──────────────────────────────────────────

    def execute(self, ctx: Any) -> bool:
        self._run(ctx)
        return True

    async def async_execute(self, ctx: Any) -> bool:
        await asyncio.to_thread(self._run, ctx)
        return True

    def _run(self, ctx: Any) -> None:
        context_manager = self._resolve_context_manager(ctx)
        if context_manager is None:
            self.output("压缩不可用：上下文管理器未初始化")
            return
        try:
            result = context_manager.compact_now()
        except Exception as error:
            _logger.debug("手动压缩失败", exc_info=True)
            self.output(self._failure_text(error))
            return
        if result is None:
            self.output("暂无可压缩的历史")
            return
        # 压缩已落地：尽力持久化会话（对齐 dsh 手动压缩后的 flush 语义）。
        self._persist(ctx)
        self.output(
            f"已压缩 {result.shadowed_count} 条历史（~{result.saved_tokens}t）"
        )

    @staticmethod
    def _persist(ctx: Any) -> None:
        session = getattr(ctx, "session", None)
        save = getattr(session, "save", None)
        if not callable(save):
            return
        try:
            save()
        except Exception:
            _logger.debug("压缩后保存会话失败", exc_info=True)

    # ── 内部辅助 ──────────────────────────────────────────

    @staticmethod
    def _resolve_context_manager(ctx: Any):
        session = getattr(ctx, "session", None)
        context_manager = getattr(session, "context_manager", None) if session is not None else None
        if context_manager is None:
            context_manager = getattr(ctx, "context_manager", None)
        return context_manager

    @staticmethod
    def _failure_text(error: Exception) -> str:
        code = getattr(error, "code", None)
        code_value = getattr(code, "value", code)
        known = _FAILURE_TEXT.get(code_value)
        if known is not None:
            return known
        return f"压缩失败: {error}"


declare_command_plugin(CompactPlugin())
