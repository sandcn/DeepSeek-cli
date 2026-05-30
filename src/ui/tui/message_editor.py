"""
交互式会话消息编辑器 — 在底部栏补全弹窗中操作。

用法：在聊天中输入 /editmsg 或 Ctrl+O 进入消息编辑。

编辑职责：
- 消息选择交互（底部栏补全弹窗 + raw I/O）
- 编辑/删除/恢复动作处理
- 会话管理入口（MessageEditor.edit_current_messages）

显示输出统一通过 _disp.write_line() 动态解析，
确保 set_message_output() 注入后即时生效。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from ..colors import DIM, GREEN, RESET, YELLOW
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
from ...api.interrupt_async import flush_stdin, reset_interrupt_async
from .._lock import locked_print
from .._bottom_bar import run_bottom_bar_selection
from . import _message_display as _disp

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 模块级沙盒恢复（无状态纯函数）
# ═══════════════════════════════════════════════════════════

def _restore_sandbox_to(agent: Any, target_idx: int) -> str:
    """恢复沙盒到指定消息索引，返回恢复文件数的描述文本。"""
    sandbox_manager = _get_sandbox_manager()
    if not sandbox_manager:
        return ""
    results = sandbox_manager.restore_to_message(target_idx)
    if results:
        restored = sum(1 for success in results.values() if success)
        return f"已恢复 {restored} 个文件"
    return ""


def _truncate_text(text: str, max_len: int = 40) -> str:
    """截断文本到指定长度（不包括 ANSI 码计算）。"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _msg_short_summary(msg: dict) -> str:
    """生成消息的简短摘要（单行，适合弹窗显示）。

    格式: [U] 用户消息前20字...
          [A] 助手回复前20字...
          [T] 工具调用: func_name
    """
    role = msg.get("role", "?")
    content = msg.get("content", "") or ""
    if role == "user":
        text = content.replace("\n", " ").strip()
        return f"[U] {_truncate_text(text, 35)}"
    elif role == "assistant":
        if content:
            text = content.replace("\n", " ").strip()
            return f"[A] {_truncate_text(text, 35)}"
        # tool_calls
        tcs = msg.get("tool_calls", [])
        if tcs:
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tcs[:2])
            return f"[A] \u2699 {_truncate_text(names, 30)}"
        return "[A] (\u7a7a)"
    elif role == "tool":
        text = content.replace("\n", " ").strip()
        name = msg.get("name", "")
        prefix = f"{name}: " if name else ""
        return f"[T] {prefix}{_truncate_text(text, 30)}"
    else:
        text = content.replace("\n", " ").strip()
        return f"[{role[0].upper()}] {_truncate_text(text, 35)}"


def _build_message_items(data: list[dict]) -> list[str]:
    """构建消息列表的显示文本，每条一行摘要。"""
    items = []
    for i, m in enumerate(data):
        summary = _msg_short_summary(m)
        items.append(f"{i}. {summary}")
    return items


# ═══════════════════════════════════════════════════════════
# MessageEditor 类
# ═══════════════════════════════════════════════════════════

class MessageEditor:
    """交互式消息编辑器 — 在底部栏补全弹窗中选择消息，回车编辑，Esc 取消。

    edit_current_messages() 作为公开入口点。
    """

    # ── 消息选择交互 ────────────────────────────────────

    def _interactive_message_select(
        self,
        ctx: _disp.MessageDisplayContext,
        title: str,
        is_current: bool = False,
    ) -> tuple[str, int]:
        """在底部栏补全弹窗中选择消息，回车编辑，Esc 取消。

        Args:
            ctx: 消息显示上下文。
            title: 显示标题。
            is_current: 是否为当前会话。

        Returns:
            (action, real_idx): action = "edit"|"quit"
        """
        data = ctx.data
        if not data:
            return ("quit", 0)

        tag = " (\u5f53\u524d)" if is_current else ""  # (当前)

        # 只有 user 消息可选
        selectable = [i for i, m in enumerate(data) if m.get("role") == "user"]
        if not selectable:
            _disp.write_line(f"  {YELLOW}\u6ca1\u6709\u53ef\u7f16\u8f91\u7684\u7528\u6237\u6d88\u606f{RESET}")
            return ("quit", 0)

        sel_count = len(selectable)

        # 构建显示项：为每个可选消息生成摘要
        display_items = _build_message_items(data)
        user_display = [display_items[i] for i in selectable]

        result = run_bottom_bar_selection(
            selectable, user_display,
            initial_idx=sel_count - 1,
            title=f"{title}{tag}  {sel_count} \u6761\u6d88\u606f",  # N 条消息
        )

        if result["action"] == "cancel" or result["action"] == "error":
            return ("quit", 0)

        if result["index"] is None or result["index"] >= len(selectable):
            return ("quit", 0)

        real_idx = selectable[result["index"]]
        return ("edit", real_idx)

    # ── 动作处理 ────────────────────────────────────────

    def _handle_edit_action(
        self, agent: Any, state: dict, cursor: int, idx_map: list[int],
    ) -> bool:
        """处理 edit action：截断到光标消息之前，预填旧内容。"""
        real_idx = idx_map[cursor]
        old_content = agent.messages[real_idx].get("content") or ""
        target_index = real_idx - 1 if real_idx > 0 else 0
        restore_text = _restore_sandbox_to(agent, target_index)
        if restore_text:
            _disp.write_line(
                f"  {GREEN}{restore_text}\u5230\u6d88\u606f #{target_index} \u7684\u72b6\u6001{RESET}"
            )
        del agent.messages[real_idx:]
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        _disp.write_line(
            f"  {GREEN}\u5df2\u622a\u65ad\u5230\u6d88\u606f #{cursor} \u4e4b\u524d\uff08\u4fdd\u7559 {len(ctx.data)} \u6761\uff09{RESET}"
        )
        if cursor > len(ctx.data):
            _logger.warning("cursor=%d \u8d85\u51fa data \u8303\u56f4(%d)\uff0c\u56de\u9000", cursor, len(ctx.data))
            return False
        _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
        state["prefill"] = old_content
        return True

    def _handle_delete_action(
        self, agent: Any, cursor: int, idx_map: list[int],
    ) -> bool:
        """处理 delete action：确认后删除光标消息及之后所有消息。"""
        real_idx = idx_map[cursor]
        try:
            msg_preview = _disp._truncate(
                agent.messages[real_idx].get("content", "")
                or agent.messages[real_idx].get("role", ""),
                30,
            )
            locked_print(
                f"  {YELLOW}\u786e\u8ba4\u5220\u9664\u300c{msg_preview}\u300d\u53ca\u4e4b\u540e\u6240\u6709\u6d88\u606f\uff1f(y/N): {RESET}"
            )
            confirm = input().strip()
        except (OSError, ValueError, Exception):
            confirm = ""
        if confirm.lower() != 'y':
            return False
        target_index = real_idx - 1 if real_idx > 0 else 0
        restore_text = _restore_sandbox_to(agent, target_index)
        if restore_text:
            _disp.write_line(
                f"  {GREEN}{restore_text}\u5230\u6d88\u606f #{target_index} \u7684\u72b6\u6001{RESET}"
            )
        removed = len(agent.messages) - real_idx
        del agent.messages[real_idx:]
        _disp.write_line(
            f"  {GREEN}\u5df2\u5220\u9664 {removed} \u6761\u6d88\u606f{RESET}"
        )
        _disp.write_line(
            f"  {DIM}\u7ee7\u7eed\u8f93\u5165\u5f00\u59cb\u5bf9\u8bdd{RESET}"
        )
        return True

    def _check_last_message_role(self, agent: Any, state: dict) -> None:
        """检查最后一条消息角色，设置重试提示。"""
        if not agent.messages:
            _disp.write_line(f"  {DIM}\u7ee7\u7eed\u8f93\u5165\u5f00\u59cb\u5bf9\u8bdd{RESET}")
            return
        last_role = agent.messages[-1].get("role", "?")
        if last_role == "user":
            _disp.write_line(
                f"  {DIM}\u6700\u540e\u4e00\u6761\u662f\u7528\u6237\u6d88\u606f\uff0c\u5c06\u81ea\u52a8\u7ee7\u7eed\u751f\u6210\u56de\u590d\u2026{RESET}"
            )
            state["retry"] = True
        else:
            _disp.write_line(f"  {DIM}\u7ee7\u7eed\u8f93\u5165\u5f00\u59cb\u5bf9\u8bdd{RESET}")

    def _handle_resume_action(
        self, agent: Any, state: dict, cursor: int, idx_map: list[int],
    ) -> bool:
        """处理 resume action：截断到光标消息之后，保留当前消息。"""
        real_idx = idx_map[cursor]
        restore_text = _restore_sandbox_to(agent, real_idx)
        if restore_text:
            _disp.write_line(
                f"  {GREEN}{restore_text}\u5230\u6d88\u606f #{real_idx} \u7684\u72b6\u6001{RESET}"
            )
        del agent.messages[real_idx + 1:]
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        remaining = len(ctx.data)
        _disp.write_line(
            f"  {GREEN}\u5df2\u622a\u65ad\u5230\u6d88\u606f #{cursor}\uff08\u4fdd\u7559 {remaining} \u6761\uff09{RESET}"
        )
        _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
        self._check_last_message_role(agent, state)
        return True

    def _handle_resume_all_action(self, agent: Any, state: dict) -> bool:
        """处理 resume_all action：恢复全部消息，不做截断。"""
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        _disp.write_line(
            f"  {GREEN}\u5df2\u6062\u590d\u5168\u90e8\u6d88\u606f\uff08\u5171 {len(ctx.data)} \u6761\uff09{RESET}"
        )
        _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
        self._check_last_message_role(agent, state)
        return True

    # ── 会话管理 ────────────────────────────────────────

    def _current_session_detail(self, agent: Any, state: dict) -> bool:
        """选择消息并编辑。"""
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        if not ctx.data:
            _disp.write_line(
                f"  {YELLOW}\u5f53\u524d\u4f1a\u8bdd\u4e3a\u7a7a{RESET}"
            )
            return False

        action, cursor = self._interactive_message_select(
            ctx, "\u5f53\u524d\u4f1a\u8bdd", is_current=True,
        )
        if action == "edit":
            return self._handle_edit_action(agent, state, cursor, ctx.idx_map)
        return False

    # ── 公开入口 ────────────────────────────────────────

    def edit_current_messages(self, agent: Any, state: dict) -> bool:
        """进入当前会话消息编辑（Ctrl+O / /editmsg）。

        Args:
            agent: ChatAgent 实例（包含 messages 列表）。
            state: 编辑状态字典，用于传递重试/预填等标记。

        Returns:
            True 表示有修改（调用方应重新发送/继续），False 表示无操作。
        """
        ctx = _disp.MessageDisplayContext.from_messages(agent.messages)
        if not ctx.data:
            _disp.write_line(
                f"  {YELLOW}\u5f53\u524d\u4f1a\u8bdd\u4e3a\u7a7a\uff0c\u65e0\u6d88\u606f\u53ef\u7f16\u8f91{RESET}"
            )
            return False
        return self._current_session_detail(agent, state)


# ═══════════════════════════════════════════════════════════
# 向后兼容入口（模块级函数 → 委托 MessageEditor 实例）
# ═══════════════════════════════════════════════════════════


def edit_current_messages(agent: Any, state: dict) -> bool:
    """直接进入当前会话消息编辑（模块级入口，向后兼容）。

    Args:
        agent: ChatAgent 实例。
        state: 编辑状态字典。

    Returns:
        True 表示有修改，False 表示无操作。
    """
    return MessageEditor().edit_current_messages(agent, state)


# 保持 display_messages 向后兼容（从 _message_display 重新导出）
from ._message_display import display_messages  # noqa: F401


__all__ = [
    "MessageEditor",
    "edit_current_messages",
    "display_messages",
]
