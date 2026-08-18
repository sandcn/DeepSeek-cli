"""应用主循环消息处理器 — 从 app_loop.py 拆分

包含独立的消息处理函数：retry、model 命令处理器。
"""

from __future__ import annotations

import asyncio
import logging

from ..core.commands import handle_command

_logger = logging.getLogger(__name__)


async def _handle_retry_sentinel(session) -> None:
    """处理 retry 哨兵"""
    await session.retry()


async def _handle_model_cmd(
    content: str,
    session,
    state,
) -> None:
    """处理 /model 命令（无参数时使用底部栏补全弹窗交互选择）

    暂停 ChatUIConsumer + 停止 EscapeMonitor（join 线程、恢复 cooked 终端），
    让底部栏补全弹窗 + raw I/O 处理 ↑↓/Enter/Esc 交互，选择完成后恢复两者。
    """
    from ..tui.consumer import get_active_chat_ui
    from ..api.escape_monitor import get_active_monitor
    chat_ui = get_active_chat_ui()
    monitor = get_active_monitor()
    if chat_ui is not None:
        chat_ui.suspend()
    if monitor is not None:
        monitor.stop()
    try:
        state_dict = {"model": state.model, "retry": False, "prefill": ""}

        def _stream_input(default: str = "", show_prompt: bool = True) -> str:
            return default

        from ..core.commands import CommandUiAdapter
        cmd_handled = await asyncio.to_thread(
            handle_command,
            content, session.messages, state_dict,
            session.agent.build_system_prompt,
            _stream_input,
            session.context_manager,
            session,
            CommandUiAdapter(),
        )
        if cmd_handled:
            new_model = state_dict.get("model")
            if new_model and new_model != session.model:
                session.model = new_model
            state.model = state_dict.get("model", state.model)
            state.retry = state_dict.get("retry", False)
            state.prefill = state_dict.get("prefill", "")
    finally:
        if monitor is not None:
            monitor.start()
        if chat_ui is not None:
            chat_ui.resume()
