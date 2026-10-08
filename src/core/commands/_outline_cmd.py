"""outline 命令 — ``/outline`` 消息大纲导航视图（全屏视图 / 文本回退）。

有活跃 ChatUI 时打开全屏「消息大纲」（``model.fullscreen == "outline"``）；
无 ChatUI 回退文本列出消息大纲。

数据源：``ctx.messages``（排除 system）——命令线程构建节点摘要注入视图；
跳转经 ``jump_seq`` / ``jump_target`` 回传，命令线程在聊天区回显定位行。
"""

from __future__ import annotations

import logging

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, RESET, YELLOW
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()

_ROLE_LABEL = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}


def _build_outline_entries(ctx) -> list:
    """会话消息 → 大纲节点列表。"""
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
        tools: list = []
        for tc in (m.get("tool_calls") or []):
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name")
                if name:
                    tools.append(str(name))
        if role == "tool" and not text:
            text = str(m.get("name", "") or "")
        summary = " ".join(text.split())[:80]
        if not summary:
            summary = f"[{len(tools)} 个工具调用]" if tools else "(空)"
        out.append({
            "index": i, "role": role, "summary": summary,
            "text": text, "tools": tools,
        })
    return out


def _open_outline_ui(ctx) -> bool:
    """打开全屏消息大纲视图（有 ChatUI 时）。"""
    from ..adapters.ui_runtime import get_active_chat_ui, get_outline_view_state_cls
    from ._view_opener import open_fullscreen_view

    chat_ui = get_active_chat_ui()
    jump = {"v": 0}

    def setup(model, state):
        state.entries = _build_outline_entries(ctx)

    def tick(state) -> bool:
        if state.jump_seq > jump["v"]:
            jump["v"] = state.jump_seq
            target = state.jump_target
            node = next(
                (e for e in (state.entries or [])
                 if int(e.get("index", -1)) == int(target if target is not None else -1)),
                None,
            )
            if node is not None and chat_ui is not None:
                summary = str(node.get("summary", ""))[:80]
                try:
                    chat_ui.write_line(
                        f"  {DIM}\u2192 消息 #{node.get('index')} "
                        f"({_ROLE_LABEL.get(node.get('role', ''), node.get('role', ''))}): "
                        f"{summary}{RESET}"
                    )
                except Exception:
                    _logger.debug("大纲跳转回显失败", exc_info=True)
            return True
        return False

    return open_fullscreen_view(
        ctx, view_id="outline", state_attr="outline_view",
        state_cls=get_outline_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="消息大纲已关闭", timeout_hint="消息大纲超时关闭",
    )


def _outline_text(ctx) -> bool:
    """文本列出消息大纲（无 ChatUI / 单次模式回退）。"""
    entries = _build_outline_entries(ctx)
    if not entries:
        _out.write(f"{DIM}  - 无消息{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{DIM}  \u2500 消息大纲{RESET}", level="raw", source="cmd")
    for e in entries:
        label = _ROLE_LABEL.get(e.get("role", ""), e.get("role", ""))
        tools = e.get("tools") or []
        suffix = f"  [{len(tools)} 工具]" if tools else ""
        _out.write(
            f"  {CYAN}#{e.get('index'):>3}{RESET}  {label}  {DIM}{e.get('summary', '')}{suffix}{RESET}",
            level="raw", source="cmd",
        )
    return True


def _cmd_outline(ctx) -> bool:
    """打开消息大纲视图（有 ChatUI）或文本列出（回退）。"""
    if _open_outline_ui(ctx):
        return True
    return _outline_text(ctx)


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class OutlineCommand(CommandPlugin):
    """消息大纲 / 导航（/outline）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="outline", description="消息大纲导航（节点跳转）", group="ui",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_outline(ctx)


declare_command_plugin(OutlineCommand())
