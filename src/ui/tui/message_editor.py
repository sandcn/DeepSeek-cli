"""
交互式会话消息编辑器 — 上下键选择、编辑、恢复当前会话消息。

用法：在聊天中输入 /editmsg 或 Ctrl+O 进入消息编辑。

编辑职责（显示职责委托给 _message_display 模块）：
- 消息选择交互（MessageEditor._interactive_message_select）
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


# ═══════════════════════════════════════════════════════════
# MessageEditor 类
# ═══════════════════════════════════════════════════════════

class MessageEditor:
    """交互式消息编辑器 — 封装消息编辑/删除/恢复的完整工作流。

    通过构造函数注入 Picker 工厂（可选），消除模块级可变状态。
    edit_current_messages() 作为公开入口点，内部循环选择+动作处理。

    用法：
        editor = MessageEditor()
        editor.edit_current_messages(agent, state)

        # 或通过依赖注入：
        editor = MessageEditor(picker_factory=custom_factory)
        editor.edit_current_messages(agent, state)
    """

    _MAX_ITERATIONS = 50

    def __init__(self, picker_factory: Callable | None = None) -> None:
        """初始化 MessageEditor。

        Args:
            picker_factory: Picker 工厂函数，签名 (title, items, **kwargs) -> Picker。
                            省略时从 ui.picker 惰性导入默认实现。
        """
        self._picker_factory = picker_factory

    def _make_picker(self, title: str, items: list, **kwargs) -> object:
        """创建 Picker 实例。

        优先级：实例级工厂 > 默认 Picker 惰性导入。
        """
        if self._picker_factory is not None:
            return self._picker_factory(title=title, items=items, **kwargs)
        from ..picker import Picker as _P
        return _P(title=title, items=items, **kwargs)

    # ── 消息选择交互 ────────────────────────────────────

    def _interactive_message_select(
        self,
        ctx: _disp.MessageDisplayContext,
        title: str,
        is_current: bool = False,
    ) -> tuple[str, int]:
        """对会话内的消息做上下键选择（仅 user 消息可选）。

        Args:
            ctx: 消息显示上下文（封装 data / agent / idx_map）。
            title: 显示标题。
            is_current: 是否为当前会话。

        Returns:
            (action, cursor_idx): action = "edit" | "delete" | "resume" | "resume_all" | "quit"
        """
        data = ctx.data
        if not data:
            return ("quit", 0)

        tag = " (当前)" if is_current else ""
        selectable = [i for i, m in enumerate(data) if m.get("role") == "user"]
        if not selectable:
            _disp.write_line(f"  {YELLOW}没有可编辑的用户消息{RESET}")
            return ("quit", 0)

        sel_count = len(selectable)

        def make_lines(items, cursor, st):
            return _disp._make_message_lines(
                items, cursor, st, ctx, title, tag, is_current,
            )

        def keys(kb, st):
            @kb.add("enter")
            def _edit(e):
                st["action"] = "edit"
                e.app.exit()

            @kb.add("r")
            def _resume(e):
                st["action"] = "resume"
                e.app.exit()

            @kb.add("R")
            def _resume_all(e):
                st["action"] = "resume_all"
                e.app.exit()

            @kb.add("d")
            def _del(e):
                st["action"] = "delete"
                e.app.exit()

        picker = self._make_picker(
            title=f"{title}{tag}  {sel_count} 条消息",
            items=selectable,
            make_lines=make_lines,
            key_setup=keys,
            initial_cursor=sel_count - 1,
        )
        flush_stdin()
        reset_interrupt_async()
        try:
            result = picker.run()
        except Exception as e:
            _logger.error("Picker 运行异常: %s", e)
            _disp.write_line(
                f"  {YELLOW}消息编辑器异常退出: {e}{RESET}"
            )
            # 异常退出时清理 stdin 残留
            flush_stdin()
            reset_interrupt_async()
            return ("quit", 0)
        action = result.action or "quit"
        if action == "cancel":
            action = "quit"
        real_idx = selectable[result.selected_indices[0]] if result.selected_indices else 0
        return (action, real_idx)

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
                f"  {GREEN}{restore_text}到消息 #{target_index} 的状态{RESET}"
            )
        del agent.messages[real_idx:]
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        _disp.write_line(
            f"  {GREEN}已截断到消息 #{cursor} 之前（保留 {len(ctx.data)} 条）{RESET}"
        )
        if cursor > len(ctx.data):
            _logger.warning("cursor=%d 超出 data 范围(%d)，回退", cursor, len(ctx.data))
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
                f"  {YELLOW}确认删除「{msg_preview}」及之后所有消息？(y/N): {RESET}"
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
                f"  {GREEN}{restore_text}到消息 #{target_index} 的状态{RESET}"
            )
        removed = len(agent.messages) - real_idx
        del agent.messages[real_idx:]
        _disp.write_line(
            f"  {GREEN}已删除 {removed} 条消息{RESET}"
        )
        _disp.write_line(
            f"  {DIM}继续输入开始对话{RESET}"
        )
        return True

    def _check_last_message_role(self, agent: Any, state: dict) -> None:
        """检查最后一条消息角色，设置重试提示。"""
        if not agent.messages:
            _disp.write_line(f"  {DIM}继续输入开始对话{RESET}")
            return
        last_role = agent.messages[-1].get("role", "?")
        if last_role == "user":
            _disp.write_line(
                f"  {DIM}最后一条是用户消息，将自动继续生成回复…{RESET}"
            )
            state["retry"] = True
        else:
            _disp.write_line(f"  {DIM}继续输入开始对话{RESET}")

    def _handle_resume_action(
        self, agent: Any, state: dict, cursor: int, idx_map: list[int],
    ) -> bool:
        """处理 resume action：截断到光标消息之后，保留当前消息。"""
        real_idx = idx_map[cursor]
        restore_text = _restore_sandbox_to(agent, real_idx)
        if restore_text:
            _disp.write_line(
                f"  {GREEN}{restore_text}到消息 #{real_idx} 的状态{RESET}"
            )
        # 边界情况：real_idx 是最后一条消息时切片 [real_idx+1:] 为空，
        # del 空列表无效果——此时仅恢复沙盒，不截断消息
        del agent.messages[real_idx + 1:]
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        remaining = len(ctx.data)
        _disp.write_line(
            f"  {GREEN}已截断到消息 #{cursor}（保留 {remaining} 条）{RESET}"
        )
        _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
        self._check_last_message_role(agent, state)
        return True

    def _handle_resume_all_action(self, agent: Any, state: dict) -> bool:
        """处理 resume_all action：恢复全部消息，不做截断。"""
        ctx = _disp.MessageDisplayContext.from_agent(agent)
        _disp.write_line(
            f"  {GREEN}已恢复全部消息（共 {len(ctx.data)} 条）{RESET}"
        )
        _disp.display_messages(ctx.data, ctx.agent, ctx.idx_map, speed=0)
        self._check_last_message_role(agent, state)
        return True

    # ── 会话管理 ────────────────────────────────────────

    def _current_session_detail(self, agent: Any, state: dict) -> bool:
        """交互式编辑当前会话消息。"""
        for _ in range(self._MAX_ITERATIONS):
            ctx = _disp.MessageDisplayContext.from_agent(agent)
            if not ctx.data:
                _disp.write_line(
                    f"  {YELLOW}当前会话为空{RESET}"
                )
                return False

            action, cursor = self._interactive_message_select(
                ctx, "当前会话", is_current=True,
            )

            if action == "quit":
                return False
            if action == "edit":
                return self._handle_edit_action(agent, state, cursor, ctx.idx_map)
            if action == "delete":
                return self._handle_delete_action(agent, cursor, ctx.idx_map)
            if action == "resume":
                return self._handle_resume_action(agent, state, cursor, ctx.idx_map)
            if action == "resume_all":
                return self._handle_resume_all_action(agent, state)

        _logger.warning(
            "_current_session_detail 循环超过 %d 次，强制退出", _MAX_ITERATIONS
        )
        _disp.write_line(
            f"  {YELLOW}编辑循环次数超限，已退出{RESET}"
        )
        return False

    # ── 公开入口 ────────────────────────────────────────

    def edit_current_messages(self, agent: Any, state: dict) -> bool:
        """进入当前会话消息编辑（Ctrl+O / /editmsg）。

        检查数据有效性后委托给 _current_session_detail 循环。

        Args:
            agent: ChatAgent 实例（包含 messages 列表）。
            state: 编辑状态字典，用于传递重试/预填等标记。

        Returns:
            True 表示有修改（调用方应重新发送/继续），False 表示无操作。
        """
        ctx = _disp.MessageDisplayContext.from_messages(agent.messages)
        if not ctx.data:
            _disp.write_line(
                f"  {YELLOW}当前会话为空，无消息可编辑{RESET}"
            )
            return False
        return self._current_session_detail(agent, state)


# ═══════════════════════════════════════════════════════════
# 向后兼容入口（模块级函数 → 委托 MessageEditor 实例）
# ═══════════════════════════════════════════════════════════


def edit_current_messages(agent: Any, state: dict) -> bool:
    """直接进入当前会话消息编辑（模块级入口，向后兼容）。

    每次调用创建新的 MessageEditor 实例（构造函数开销小，无重型初始化）。
    需要自定义 Picker 工厂时，使用 MessageEditor(picker_factory=...) 直接构造。

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
