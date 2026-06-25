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

from ...core.constants import CYAN, DIM, GREEN, RESET, YELLOW, BRIGHT_CYAN, \
    BRIGHT_GREEN, DARK_GRAY, BOLD, BLUE
from ...ui.theme import THEME
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
from ...api.interrupt_async import flush_stdin, reset_interrupt_async
from ...ui._lock import locked_print
from ...ui._bottom_bar_selection import run_bottom_bar_selection
from ...ui.events import publish_output
from ..infrastructure import message_display as _disp
from ...ui.common.text_utils import truncate

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


def _msg_short_summary(msg: dict) -> str:
    """生成消息的简短摘要（单行，适合弹窗显示）。

    格式: ● │ 用户消息前35字...  (亮青色)
          ◆ │ 助手回复前35字...  (亮绿色)
          ◆ ⚙ func_name          (助手带 tool_calls)
          ⚙ name: 内容前30字      (工具消息, 深灰)
          · 其他角色消息前35字... (蓝色圆点)

    角色图标使用语义化符号，与消息显示对齐。
    """
    role = msg.get("role", "?")
    content = msg.get("content", "") or ""
    icon_map = {"user": "\u25cf", "assistant": "\u25c6", "tool": "\u2699"}
    icon = icon_map.get(role, "\u00b7")
    if role == "user":
        text = content.replace("\n", " ").strip()
        # ★ 美化：用户消息用亮青色 + 竖线装饰
        return f"{BRIGHT_CYAN}{icon}{RESET} {BRIGHT_CYAN}\u2502{RESET} {truncate(text, 35)}"
    elif role == "assistant":
        if content:
            text = content.replace("\n", " ").strip()
            return f"{BRIGHT_GREEN}{icon}{RESET} {BRIGHT_GREEN}\u2502{RESET} {truncate(text, 35)}"
        # tool_calls
        tcs = msg.get("tool_calls", [])
        if tcs:
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tcs[:2])
            return f"{BRIGHT_GREEN}{icon}{RESET} {YELLOW}\u2699{RESET} {truncate(names, 30)}"
        return f"{DIM}{icon}{RESET} {DIM}(\u7a7a){RESET}"
    elif role == "tool":
        text = content.replace("\n", " ").strip()
        name = msg.get("name", "")
        prefix = f"{DARK_GRAY}{name}:{RESET} " if name else ""
        return f"{DIM}{icon}{RESET} {prefix}{DIM}{truncate(text, 30)}{RESET}"
    else:
        text = content.replace("\n", " ").strip()
        return f"{BLUE}\u00b7{RESET} {truncate(text, 35)}"


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
        bottom_bar=None,
    ) -> tuple[str, int]:
        """在底部栏补全弹窗中选择消息，回车编辑，Esc 取消。

        Args:
            ctx: 消息显示上下文。
            title: 显示标题。
            is_current: 是否为当前会话。
            bottom_bar: 底部栏实例（由调用方注入）。

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
            publish_output(
                f"  {THEME['warning']}\u6ca1\u6709\u53ef\u7f16\u8f91\u7684\u7528\u6237\u6d88\u606f{RESET}",
                level="raw", source="cmd",
            )
            return ("quit", 0)

        sel_count = len(selectable)

        # 构建显示项：为每个可选消息生成摘要
        display_items = _build_message_items(data)
        user_display = [display_items[i] for i in selectable]

        # ★ 消息选择弹窗：显示用户可选消息总数
        title_display = f"{BRIGHT_CYAN}{title}{RESET}{DIM}{tag}{RESET}  {DIM}\u2502{RESET}  {CYAN}{sel_count}{RESET} \u6761\u53ef\u7f16\u8f91"  # 当前会话(当前) │ N 条可编辑

        result = run_bottom_bar_selection(
            selectable, user_display,
            initial_idx=sel_count - 1,
            title=title_display,
            bottom_bar=bottom_bar,
        )

        if result["action"] == "cancel":
            publish_output(
                f"  {DIM}\u5df2\u53d6\u6d88\u7f16\u8f91{RESET}",
                level="raw", source="cmd",
            )
            return ("quit", 0)
        if result["action"] == "error":
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
            publish_output(
                f"  {BRIGHT_GREEN}\u2714{RESET} {restore_text}",
                level="raw", source="cmd",
            )
        _snapshot = list(agent.messages[real_idx:])
        try:
            del agent.messages[real_idx:]
            ctx = _disp.MessageDisplayContext.from_agent(agent)
            publish_output(
                f"  {BRIGHT_GREEN}\u2714{RESET} \u5df2\u622a\u65ad\u5230\u6d88\u606f #{cursor} \uff08\u4fdd\u7559 {BRIGHT_CYAN}{len(ctx.data)}{RESET} \u6761\uff09",
                level="raw", source="cmd",
            )
            if cursor > len(ctx.data):
                _logger.warning("cursor=%d \u8d85\u51fa data \u8303\u56f4(%d)\uff0c\u56de\u9000", cursor, len(ctx.data))
                publish_output(
                    f"  {YELLOW}\u26a0{RESET} \u5185\u90e8\u9519\u8bef: cursor={cursor} \u8d85\u51fa data \u8303\u56f4({len(ctx.data)})",
                    level="raw", source="cmd",
                )
                return False
            _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
            state["prefill"] = old_content
            return True
        except Exception:
            _logger.exception("编辑操作异常，恢复消息快照")
            agent.messages.extend(_snapshot)
            publish_output(
                f"  {THEME['warning']}\u26a0{RESET} 编辑操作失败，已恢复消息",
                level="raw", source="cmd",
            )
            return False

    def _handle_delete_action(
        self, agent: Any, cursor: int, idx_map: list[int],
    ) -> bool:
        """处理 delete action：确认后删除光标消息及之后所有消息。"""
        real_idx = idx_map[cursor]
        try:
            msg_preview = truncate(
                agent.messages[real_idx].get("content", "").strip()
                or agent.messages[real_idx].get("role", ""),
                30,
            )
            locked_print(
                f"  {THEME['warning']}\u786e\u8ba4\u5220\u9664\u300c{msg_preview}\u300d\u53ca\u4e4b\u540e\u6240\u6709\u6d88\u606f\uff1f(y/N): {RESET}"
            )
            confirm = input().strip()
        except (OSError, ValueError, Exception) as exc:
            import traceback
            publish_output(
                f"  {YELLOW}\u26a0{RESET} \u5220\u9664\u64cd\u4f5c\u5f02\u5e38: {exc}",
                level="raw", source="cmd",
            )
            publish_output(
                f"  {DIM}{traceback.format_exc()}{RESET}",
                level="raw", source="cmd",
            )
            confirm = ""
        if confirm.lower() != 'y':
            return False
        target_index = real_idx - 1 if real_idx > 0 else 0
        restore_text = _restore_sandbox_to(agent, target_index)
        if restore_text:
            publish_output(
                f"  {BRIGHT_GREEN}\u2714{RESET} {restore_text}",
                level="raw", source="cmd",
            )
        removed = len(agent.messages) - real_idx
        _snapshot = list(agent.messages[real_idx:])
        try:
            del agent.messages[real_idx:]
            publish_output(
                f"  {YELLOW}\u2716{RESET} \u5df2\u5220\u9664 {BRIGHT_CYAN}{removed}{RESET} \u6761\u6d88\u606f",
                level="raw", source="cmd",
            )
            publish_output(
                f"  {DIM}\u2514 \u7ee7\u7eed\u8f93\u5165\u5f00\u59cb\u5bf9\u8bdd{RESET}",
                level="raw", source="cmd",
            )
            return True
        except Exception:
            _logger.exception("删除操作异常，恢复消息快照")
            agent.messages.extend(_snapshot)
            publish_output(
                f"  {THEME['warning']}\u26a0{RESET} 删除操作失败，已恢复消息",
                level="raw", source="cmd",
            )
            return False

    def _check_last_message_role(self, agent: Any, state: dict) -> None:
        """检查最后一条消息角色，设置重试提示。"""
        if not agent.messages:
            publish_output(
                f"  {DIM}\u2514 \u7ee7\u7eed\u8f93\u5165\u5f00\u59cb\u5bf9\u8bdd{RESET}",
                level="raw", source="cmd",
            )
            return
        last_role = agent.messages[-1].get("role", "?")
        if last_role == "user":
            publish_output(
                f"  {BRIGHT_CYAN}\u25b6{RESET} \u6700\u540e\u4e00\u6761\u662f\u7528\u6237\u6d88\u606f\uff0c\u5c06\u81ea\u52a8\u7ee7\u7eed\u751f\u6210\u56de\u590d\u2026",
                level="raw", source="cmd",
            )
            state["retry"] = True
        else:
            publish_output(
                f"  {DIM}\u2514 \u7ee7\u7eed\u8f93\u5165\u5f00\u59cb\u5bf9\u8bdd{RESET}",
                level="raw", source="cmd",
            )

    def _handle_resume_action(
        self, agent: Any, state: dict, cursor: int, idx_map: list[int],
    ) -> bool:
        """处理 resume action：截断到光标消息之后，保留当前消息。"""
        real_idx = idx_map[cursor]
        restore_text = _restore_sandbox_to(agent, real_idx)
        if restore_text:
            publish_output(
                f"  {BRIGHT_GREEN}\u2714{RESET} {restore_text}",
                level="raw", source="cmd",
            )
        _snapshot = list(agent.messages[real_idx + 1:])
        try:
            del agent.messages[real_idx + 1:]
            ctx = _disp.MessageDisplayContext.from_agent(agent)
            remaining = len(ctx.data)
            publish_output(
                f"  {BRIGHT_GREEN}\u2714{RESET} \u5df2\u622a\u65ad\u5230\u6d88\u606f #{cursor} \uff08\u4fdd\u7559 {BRIGHT_CYAN}{remaining}{RESET} \u6761\uff09",
                level="raw", source="cmd",
            )
            _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
            self._check_last_message_role(agent, state)
            return True
        except Exception:
            _logger.exception("恢复操作异常，恢复消息快照")
            agent.messages.extend(_snapshot)
            publish_output(
                f"  {THEME['warning']}\u26a0{RESET} 恢复操作失败，已恢复消息",
                level="raw", source="cmd",
            )
            return False

    def _handle_resume_all_action(self, agent: Any, state: dict) -> bool:
        """处理 resume_all action：恢复全部消息，不做截断。"""
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        publish_output(
            f"  {BRIGHT_GREEN}\u2714{RESET} \u5df2\u6062\u590d\u5168\u90e8\u6d88\u606f\uff08\u5171 {BRIGHT_CYAN}{len(ctx.data)}{RESET} \u6761\uff09",
            level="raw", source="cmd",
        )
        _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
        self._check_last_message_role(agent, state)
        return True

    # ── 会话管理 ────────────────────────────────────────

    # ── 公开入口 ────────────────────────────────────────

    def edit_current_messages(self, agent: Any, state: dict, bottom_bar=None) -> bool:
        """进入当前会话消息编辑（Ctrl+O / /editmsg）。

        Args:
            agent: ChatAgent 实例（包含 messages 列表）。
            state: 编辑状态字典，用于传递重试/预填等标记。
            bottom_bar: 底部栏实例（由调用方注入）。

        Returns:
            True 表示有修改（调用方应重新发送/继续），False 表示无操作。
        """
        ctx = _disp.MessageDisplayContext.from_messages(agent.messages)
        if not ctx.data:
            publish_output(
                f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u4e3a\u7a7a\uff0c\u65e0\u6d88\u606f\u53ef\u7f16\u8f91",
                level="raw",
                source="cmd",
            )
            return False
        return self._current_session_detail(agent, state, bottom_bar)

    def _current_session_detail(self, agent: Any, state: dict, bottom_bar=None) -> bool:
        """选择消息并编辑。"""
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        if not ctx.data:
            publish_output(
                f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u4e3a\u7a7a",
                level="raw", source="cmd",
            )
            return False

        action, cursor = self._interactive_message_select(
            ctx, "\u5f53\u524d\u4f1a\u8bdd", is_current=True, bottom_bar=bottom_bar,
        )
        if action == "edit":
            return self._handle_edit_action(agent, state, cursor, ctx.idx_map)
        return False




__all__ = [
    "MessageEditor",
]
