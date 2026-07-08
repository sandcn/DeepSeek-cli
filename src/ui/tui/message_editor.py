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

from ..colors import CYAN, DIM, GREEN, RESET, YELLOW, BRIGHT_CYAN, \
    BRIGHT_GREEN, DARK_GRAY, BOLD, BLUE
from ..theme import THEME
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager
from ...api.interrupt_async import flush_stdin, reset_interrupt_async
from .._lock import locked_print
from .._bottom_bar import run_bottom_bar_selection
from ..events import publish_output
from . import _message_display as _disp
from ._text_utils import truncate
from ._terminal import get_terminal_width

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


def _get_editor_msg_max_width() -> int:
    """根据终端宽度计算消息摘要的最大截断宽度。

    终端宽度减去前缀边距（约 12 字符），再 clamp 到 [25, 80] 区间：
    - 宽屏（≥120 列）→ 最多 80 字符
    - 标准屏（80 列）→ 约 68 字符
    - 窄屏（≤30 列）→ 最少 25 字符

    Returns:
        计算后的最大宽度（int），异常时回退到 80。
    """
    try:
        term_width = get_terminal_width()
    except Exception:
        term_width = 80
    # 减去前缀装饰/边距约 12 字符，clamp 到 [25, 80]
    return max(25, min(term_width - 12, 80))


def _msg_short_summary(msg: dict) -> str:
    """生成消息的简短摘要（纯文本，适合弹窗显示）。

    截断宽度根据终端宽度动态计算（_get_editor_msg_max_width），
    宽屏显示更多内容，窄屏自动缩减。

    格式: ● │ 用户消息摘要...  (动态宽度 [25-80])
          ◆ │ 助手回复摘要...  (动态宽度 [25-80])
          ◆ ⚙ func_name        (助手带 tool_calls, 动态宽度 ≤45)
          ⚙ name: 内容摘要     (工具消息, 动态宽度 ≤60)
          · 其他角色消息摘要... (动态宽度 [25-80])

    注意：输出纯文本（不含 ANSI 颜色转义码），颜色由弹窗自身渲染。
    """
    role = msg.get("role", "?")
    content = msg.get("content", "") or ""
    icon_map = {"user": "\u25cf", "assistant": "\u25c6", "tool": "\u2699"}
    icon = icon_map.get(role, "\u00b7")
    max_w = _get_editor_msg_max_width()
    if role == "user":
        text = content.replace("\n", " ").strip()
        return f"{icon} \u2502 {truncate(text, max_w)}"
    elif role == "assistant":
        if content:
            text = content.replace("\n", " ").strip()
            return f"{icon} \u2502 {truncate(text, max_w)}"
        # tool_calls
        tcs = msg.get("tool_calls", [])
        if tcs:
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tcs[:2])
            return f"{icon} \u2699 {truncate(names, min(max_w, 45))}"
        return f"{icon} (\u7a7a)"
    elif role == "tool":
        text = content.replace("\n", " ").strip()
        name = msg.get("name", "")
        prefix = f"{name}: " if name else ""
        return f"{icon} {prefix}{truncate(text, min(max_w, 60))}"
    else:
        text = content.replace("\n", " ").strip()
        return f"\u00b7 {truncate(text, max_w)}"


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
            (action, real_idx): action = "edit" 表示用户确认选择, real_idx 为实际消息索引；
                                action = "quit" 表示取消/错误/无可选消息, real_idx 为 0（无效）。
        """
        data = ctx.data
        if not data:
            _logger.warning("消息选择失败: 当前会话无消息")
            publish_output(
                f"  {THEME['warning']}当前会话无消息{RESET}",
                level="raw", source="cmd",
            )
            return ("quit", 0)

        tag = " (\u5f53\u524d)" if is_current else ""  # (当前)

        # 只有 user 消息可选
        selectable = [i for i, m in enumerate(data) if m.get("role") == "user"]
        if not selectable:
            _logger.warning("消息选择失败: 没有可编辑的用户消息")
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

        # TODO: 后续可通过依赖注入传入 bottom_bar（依赖注入 DI 第二步）
        result = run_bottom_bar_selection(
            selectable, user_display,
            initial_idx=sel_count - 1,
            title=title_display,
        )

        if result["action"] == "cancel":
            publish_output(
                f"  {DIM}\u5df2\u53d6\u6d88\u7f16\u8f91{RESET}",
                level="raw", source="cmd",
            )
            return ("quit", 0)
        if result["action"] == "error":
            _logger.warning("消息选择失败: run_bottom_bar_selection 返回 error action")
            publish_output(
                f"  {THEME['warning']}消息选择失败，终端输入解析异常（如为 Cygwin/Mintty 环境，请确认终端支持 ANSI escape 序列）{RESET}",
                level="raw", source="cmd",
            )
            return ("quit", 0)

        if result["index"] is None or result["index"] >= len(selectable):
            _logger.warning("消息选择索引无效: index=%s, len=%d", result.get("index"), len(selectable))
            publish_output(
                f"  {THEME['warning']}选择索引无效{RESET}",
                level="raw", source="cmd",
            )
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
            publish_output(
                f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u4e3a\u7a7a\uff0c\u65e0\u6d88\u606f\u53ef\u7f16\u8f91",
                level="raw",
                source="cmd",
            )
            return False
        return self._current_session_detail(agent, state)

    def _current_session_detail(self, agent: Any, state: dict) -> bool:
        """选择消息并编辑。"""
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        if not ctx.data:
            publish_output(
                f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u4e3a\u7a7a",
                level="raw", source="cmd",
            )
            return False

        action, cursor = self._interactive_message_select(
            ctx, "\u5f53\u524d\u4f1a\u8bdd", is_current=True,
        )
        if action == "edit":
            return self._handle_edit_action(agent, state, cursor, ctx.idx_map)
        return False


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
