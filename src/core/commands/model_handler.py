"""/model 命令处理器

无参数时委托给 app_loop._handle_model_cmd 使用 Picker 交互选择；
有参数时返回 False，由调用方回退通用路径处理（直接切换模型）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...app_loop import SessionState
    from ...core.session import ChatSession

from .handler_base import CommandHandler


class ModelHandler(CommandHandler):
    """处理 /model 命令

    - 无参数时使用底部栏补全弹窗交互选择（需 suspend ChatUI + EscapeMonitor）
    - 有参数时直接切换，走通用路径（handle_command 处理）
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
        parts = content.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            # 无参数时使用 Picker 交互选择 → 需 suspend ChatUI + EscapeMonitor
            from ...app_loop import _handle_model_cmd

            await _handle_model_cmd(content, session, state)
            chat_ui.bottom_bar.set_model_name(state.model)
            return True

        # 有参数时直接切换，由调用方回退通用路径（无需 suspend）
        return False
