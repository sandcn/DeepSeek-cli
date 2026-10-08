"""search 命令 — ``/search`` 对话内全文搜索视图（全屏视图 / 文本回退）。

有活跃 ChatUI 时打开全屏「对话内搜索」（``model.fullscreen == "search"``）；
无 ChatUI 回退提示。

数据源：``ctx.messages``（会话消息，排除 system）——命令线程收集为
``[{index, role, text}]`` 注入视图；跳转经 ``jump_seq`` / ``jump_target``
回传，命令线程在聊天区回显定位行并关闭视图。
"""

from __future__ import annotations

import logging

from ..adapters.output import get_default_output_port
from ..constants import DIM, RESET, YELLOW
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _collect_messages(ctx) -> list:
    """会话消息 → 搜索数据（排除 system）。"""
    from ._data_cmd import _msg_text

    msgs = getattr(ctx, "messages", None) or []
    out: list = []
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", ""))
        if role == "system":
            continue
        text = _msg_text(m.get("content"))
        if not text and role == "tool":
            text = str(m.get("name", "") or "(工具结果)")
        out.append({"index": i, "role": role, "text": text})
    return out


def _open_search_ui(ctx) -> bool:
    """打开全屏对话内搜索视图（有 ChatUI 时）。"""
    from ..adapters.ui_runtime import get_active_chat_ui, get_search_view_state_cls
    from ._view_opener import open_fullscreen_view

    chat_ui = get_active_chat_ui()
    jump = {"v": 0}

    def setup(model, state):
        state.messages = _collect_messages(ctx)

    def tick(state) -> bool:
        if state.jump_seq > jump["v"]:
            jump["v"] = state.jump_seq
            target = state.jump_target
            msg = next(
                (m for m in (state.messages or [])
                 if int(m.get("index", -1)) == int(target if target is not None else -1)),
                None,
            )
            if msg is not None and chat_ui is not None:
                summary = str(msg.get("text", "")).replace("\n", " ")[:80]
                try:
                    chat_ui.write_line(
                        f"  {DIM}\u2192 消息 #{msg.get('index')} "
                        f"({msg.get('role', '')}): {summary}{RESET}"
                    )
                except Exception:
                    _logger.debug("搜索跳转回显失败", exc_info=True)
            return True
        return False

    return open_fullscreen_view(
        ctx, view_id="search", state_attr="search_view",
        state_cls=get_search_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="搜索已关闭", timeout_hint="搜索超时关闭",
    )


def _search_text(ctx) -> bool:
    """无 ChatUI：提示搜索需交互式界面。"""
    messages = _collect_messages(ctx)
    _out.write(
        f"{YELLOW}  ! 对话内搜索需要交互式界面（当前有 {len(messages)} 条非系统消息）{RESET}",
        level="raw", source="cmd",
    )
    return True


def _cmd_search(ctx) -> bool:
    """打开对话内搜索视图（有 ChatUI）或提示（回退）。"""
    if _open_search_ui(ctx):
        return True
    return _search_text(ctx)


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class SearchCommand(CommandPlugin):
    """对话内全文搜索（/search）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="search", description="对话内全文搜索（命中跳转）", group="ui",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_search(ctx)


declare_command_plugin(SearchCommand())
