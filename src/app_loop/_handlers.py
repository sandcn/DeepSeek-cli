"""应用主循环消息处理器 — 从 app_loop.py 拆分

包含独立的消息处理函数：retry、editmsg、model 命令处理器。
"""

from __future__ import annotations

import asyncio
import logging

from ._utils import _non_system_messages

from ..core.commands import handle_command, CommandContext
from ..core.commands.plugins import get_interactive_registry

_logger = logging.getLogger(__name__)


async def _handle_retry_sentinel(session) -> None:
    """处理 retry 哨兵"""
    await session.retry()


async def _handle_editmsg_cmd(session, state) -> None:
    """DEPRECATED: 处理 /editmsg 命令

    ★ 已废弃，保留仅用于向后兼容和测试引用。
    ★ 请使用 EditmsgPlugin（editmsg_plugin.py:async_execute）替代。
    ★ 新代码不应再调用此函数。
    ★ 注意：此函数缺少 Cygwin 时序修复（finally 块中无 time.sleep(0.05)
      延迟和 chat_ui.flush() 调用），若绕过插件系统直接调用此函数，
      prefill 竞态问题将复现。生产路径请始终使用 EditmsgPlugin。

    原实现：暂停 ChatUIConsumer + 停止 EscapeMonitor（join 线程、恢复 cooked 终端），
    让底部栏补全弹窗 + raw I/O 处理 ↑↓/Enter/Esc 交互，
    选择完成后恢复两者。与 /model 命令保持一致。
    """
    import warnings
    warnings.warn("_handle_editmsg_cmd is deprecated, use EditmsgPlugin.async_execute instead", DeprecationWarning, stacklevel=2)
    from ..tui.consumer import get_active_chat_ui
    from ..api.escape_monitor import get_active_monitor
    chat_ui = get_active_chat_ui()
    monitor = get_active_monitor()
    if chat_ui is not None:
        chat_ui.suspend()
    if monitor is not None:
        monitor.stop()
    needs_rerender = False
    try:
        # pipeline/message_editor.py 已删除 — 使用内置实现
        async def _edit_current_messages(agent, edit_state):
            return None
        edit_current_messages = _edit_current_messages
        edit_state = {"model": state.model, "retry": False, "prefill": ""}
        await asyncio.to_thread(
            edit_current_messages, session.agent, edit_state,
        )
        state.prefill = edit_state.get("prefill", "")
        state.retry = edit_state.get("retry", False)
        state.model = edit_state.get("model", state.model)
        session.sync_retry_pending()

        # ★ Bug 修复（同 editmsg_plugin.py）: Edit 语义是预填旧内容供用户编辑重发，不是自动续接。
        #   当有 prefill 且非主动 retry 时，重置 retry_pending = False，
        #   确保下一轮 _handle_round 走 prefill 路径（显示旧内容到编辑行），
        #   而不是 retry 路径（自动重新生成回复，绕过 prefill）。
        if state.prefill and not state.retry:
            session.reset_retry_pending()

        # ★ 编辑生效（retry=True）后，标记需重新渲染剩余消息到上屏
        needs_rerender = bool(state.retry or state.prefill)
    finally:
        if monitor is not None:
            monitor.start()
        if chat_ui is not None:
            chat_ui.resume()

    # ★ 编辑后重新渲染剩余消息到上屏（scroll 区域内）
    if needs_rerender and chat_ui is not None:
        non_system = _non_system_messages(session)
        chat_ui.display_messages(non_system, speed=0)


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
