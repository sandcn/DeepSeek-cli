"""/loop 命令处理器

委托给 InteractiveLoop._handle_loop_cmd，保持现有行为不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...app_loop import SessionState
    from ...core.session import ChatSession

from .handler_base import CommandHandler


class LoopHandler(CommandHandler):
    """处理 /loop 命令

    启动循环模式，按指定次数和提词重复执行对话轮次，
    每次执行两轮（第一轮用提词，第二轮用"继续完成所有"）。
    """

    async def handle(
        self,
        content: str,
        session: ChatSession,
        state: SessionState,
        loop: Any,
        chat_ui: Any,
        monitor: Any,
    ) -> bool:
        await loop._handle_loop_cmd(content, session, state)
        return True
