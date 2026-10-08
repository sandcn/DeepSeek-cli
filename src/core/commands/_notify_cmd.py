"""notify 命令 — ``/notify`` 通知 / 事件日志视图（全屏视图 / 文本回退）。

有活跃 ChatUI 时打开全屏「通知 / 事件日志」（``model.fullscreen == "notify"``）；
无 ChatUI 回退文本列出日志缓冲（``src.notifications.history``）。

日志缓冲由 ``notify_chat_completed``（桌面通知发送）与 TUI 的
notification / error 块写入；视图内 ``r`` 刷新 / ``c`` 清空。
"""

from __future__ import annotations

import logging

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, RESET, YELLOW
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _build_notify_entries() -> list:
    """日志缓冲 → 事件条目（最新在前）。"""
    try:
        from ...notifications.history import entries as _entries

        return list(reversed(_entries()))
    except Exception:
        return []


def _open_notify_ui(ctx) -> bool:
    """打开全屏通知 / 事件日志视图（有 ChatUI 时）。"""
    from ..adapters.ui_runtime import get_notify_view_state_cls
    from ._view_opener import open_fullscreen_view

    def setup(model, state):
        state.entries = _build_notify_entries()

    return open_fullscreen_view(
        ctx, view_id="notify", state_attr="notify_view",
        state_cls=get_notify_view_state_cls(), setup=setup,
        close_hint="通知日志已关闭", timeout_hint="通知日志超时关闭",
    )


def _notify_text() -> bool:
    """文本列出日志（无 ChatUI / 单次模式回退）。"""
    entries = _build_notify_entries()
    if not entries:
        _out.write(f"{DIM}  - 暂无通知 / 事件日志{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{DIM}  \u2500 通知 / 事件日志{RESET}", level="raw", source="cmd")
    labels = {"notify": "桌面通知", "notice": "通知", "error": "错误"}
    for e in entries:
        kind = labels.get(str(e.get("kind", "")), "事件")
        _out.write(
            f"  {CYAN}{kind}{RESET}  {e.get('title', '')}  {DIM}{e.get('body', '')[:80]}{RESET}",
            level="raw", source="cmd",
        )
    return True


def _cmd_notify(ctx) -> bool:
    """打开通知日志视图（有 ChatUI）或文本列出（回退）。"""
    if _open_notify_ui(ctx):
        return True
    return _notify_text()


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class NotifyCommand(CommandPlugin):
    """通知 / 事件日志视图（/notify）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="notify", description="通知 / 事件日志视图", group="ui",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_notify(ctx)


declare_command_plugin(NotifyCommand())
