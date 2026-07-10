"""/editmsg 命令处理器

委托给 app_loop._handle_editmsg_cmd，保持现有行为不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...app_loop import SessionState
    from ...core.session import ChatSession

from .handler_base import CommandHandler


class EditMsgHandler(CommandHandler):
    """处理 /editmsg 命令

    暂停 ChatUIConsumer + 停止 EscapeMonitor（join 线程、恢复 cooked 终端），
    让底部栏补全弹窗 + raw I/O 处理 ↑↓/Enter/Esc 交互，
    选择完成后恢复两者。与 /model 命令保持一致。
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
        from ...app_loop import _handle_editmsg_cmd

        await _handle_editmsg_cmd(session, state)
        chat_ui.bottom_bar.set_model_name(state.model)
        return True
