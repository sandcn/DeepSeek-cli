"""交互式会话消息编辑器 — 使用底部栏补全弹窗选择消息。

用法：在聊天中输入 /editmsg 或 Ctrl+O 进入消息编辑。

编辑职责：
- 消息选择交互（底部栏补全弹窗 + ↑↓/Enter）
- 编辑/删除/恢复动作处理
- 会话管理入口（MessageEditor.edit_current_messages）

适配 2026-07 TUI 重构后的架构：
  - 不复用已删除的 pipeline/message_display.py 完整版，使用内置精简替代
  - 底部栏交互使用当前 _BottomBar.show_completions() API
  - 不执行 chat_ui.suspend()（重构后 suspend 会拆除 _BottomBar）
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ...core.constants import DIM, RESET, YELLOW, BRIGHT_CYAN, BRIGHT_GREEN, GREEN
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _content_str(content: Any) -> str:
    """将 content（可能是 str 或 list[dict]）转换为纯文本字符串。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", c)))
            else:
                parts.append(str(c))
        return " ".join(parts)
    return str(content)


def _truncate(text: str, max_len: int) -> str:
    """截断文本到指定长度，超出部分用 '...' 表示。"""
    text = text.replace('\n', ' ').replace('\r', '')
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _user_msg_summary(msg: dict, idx: int, max_w: int = 60) -> str:
    """生成用户消息的简短摘要（用于底部栏弹窗显示）。

    格式: N. ● │ 消息内容摘要...

    Args:
        msg: 消息字典。
        idx: 显示编号。
        max_w: 最大宽度。

    Returns:
        纯文本摘要字符串（不含 ANSI 颜色）。
    """
    content = _content_str(msg.get("content", ""))
    text = content.strip()
    return f"{idx}. \u25cf \u2502 {_truncate(text, max_w)}"


def _restore_sandbox_to(agent: Any, target_idx: int) -> str:
    """恢复沙盒到指定消息索引，返回恢复文件数的描述文本。"""
    sandbox_manager = _get_sandbox_manager()
    if not sandbox_manager:
        return ""
    results = sandbox_manager.restore_to_message(target_idx)
    if results:
        restored = sum(1 for success in results.values() if success)
        return f"\u5df2\u6062\u590d {restored} \u4e2a\u6587\u4ef6"
    return ""


# ═══════════════════════════════════════════════════════════
# 命令类 — 封装消息编辑操作
# ═══════════════════════════════════════════════════════════

class EditCommand:
    """编辑命令：截断到光标消息，预填旧内容。"""

    def __init__(self, agent: Any, real_idx: int) -> None:
        self.agent = agent
        self.real_idx = real_idx

    def execute(self, state: dict) -> bool:
        agent = self.agent
        messages = agent.messages
        if self.real_idx < 0 or self.real_idx >= len(messages):
            return False

        old_content = _content_str(messages[self.real_idx].get("content", ""))

        # 恢复沙盒
        target_index = self.real_idx - 1 if self.real_idx > 0 else 0
        restore_text = _restore_sandbox_to(agent, target_index)

        # 截断消息
        original_len = len(messages)
        del messages[self.real_idx:]

        # 同步沙盒索引
        sm = _get_sandbox_manager()
        if sm:
            sm.remap_indices(list(range(self.real_idx, original_len)))

        state["prefill"] = old_content
        return True


class DeleteCommand:
    """删除命令：删除光标消息及之后所有消息。"""

    def __init__(self, agent: Any, real_idx: int) -> None:
        self.agent = agent
        self.real_idx = real_idx

    def execute(self, state: dict) -> bool:
        agent = self.agent
        messages = agent.messages
        if self.real_idx < 0 or self.real_idx >= len(messages):
            return False

        target_index = self.real_idx - 1 if self.real_idx > 0 else 0
        _restore_sandbox_to(agent, target_index)

        original_len = len(messages)
        removed = original_len - self.real_idx
        del messages[self.real_idx:]

        sm = _get_sandbox_manager()
        if sm:
            sm.remap_indices(list(range(self.real_idx, original_len)))

        return True


class ResumeCommand:
    """恢复命令：截断到光标消息之后（保留当前消息及之前的内容）。"""

    def __init__(self, agent: Any, real_idx: int) -> None:
        self.agent = agent
        self.real_idx = real_idx

    def execute(self, state: dict) -> bool:
        agent = self.agent
        messages = agent.messages
        if self.real_idx < 0 or self.real_idx >= len(messages):
            return False

        _restore_sandbox_to(agent, self.real_idx)

        original_len = len(messages)
        del messages[self.real_idx + 1:]

        sm = _get_sandbox_manager()
        if sm:
            sm.remap_indices(list(range(self.real_idx + 1, original_len)))

        _check_last_message_role(agent, state)
        return True


class ResumeAllCommand:
    """全部恢复命令：恢复全部消息，不做截断。"""

    def __init__(self, agent: Any) -> None:
        self.agent = agent

    def execute(self, state: dict) -> bool:
        agent = self.agent
        if not agent.messages:
            return False
        _check_last_message_role(agent, state)
        return True


def _check_last_message_role(agent: Any, state: dict) -> None:
    """检查最后一条消息角色，设置重试标记。"""
    if not agent.messages:
        return
    last_role = agent.messages[-1].get("role", "?")
    if last_role == "user":
        state["retry"] = True


# 命令注册表
_COMMANDS: dict[str, type] = {
    "edit": EditCommand,
    "delete": DeleteCommand,
    "resume": ResumeCommand,
    "resume_all": ResumeAllCommand,
}


# ═══════════════════════════════════════════════════════════
# MessageEditor
# ═══════════════════════════════════════════════════════════

class MessageEditor:
    """交互式消息编辑器 — 在底部栏补全弹窗中选择消息，Enter 编辑。

    edit_current_messages() 作为公开入口点。
    """

    def __init__(self, bottom_bar: Any = None, input_: Any = None):
        """初始化 MessageEditor。

        Args:
            bottom_bar: _BottomBar 实例（用于补全弹窗）。
            input_: Input 实例（用于检测 Enter 提交）。
        """
        self._bottom_bar = bottom_bar
        self._input = input_

    # ── 公开入口 ──

    def edit_current_messages(
        self, agent: Any, state: dict, action: str = "edit",
    ) -> bool:
        """进入当前会话消息编辑（Ctrl+O / /editmsg）。

        在线程中运行（由 asyncio.to_thread 调用），
        直接使用 time.sleep 进行轮询。

        Args:
            agent: ChatAgent 实例（包含 messages 列表）。
            state: 编辑状态字典，用于传递 prefill/retry 等标记。
            action: 编辑动作类型（"edit" / "delete" / "resume" / "resume_all"）。

        Returns:
            True 表示有修改，False 表示无操作。
        """
        messages = agent.messages
        if not messages:
            return False

        # 只显示用户消息
        user_msgs = [(i, m) for i, m in enumerate(messages) if m.get("role") == "user"]
        if not user_msgs:
            return False

        # 构建显示项
        display_items = []
        for display_idx, (orig_idx, msg) in enumerate(user_msgs):
            display_items.append(_user_msg_summary(msg, display_idx))

        # 选择要编辑的消息
        real_idx = self._interactive_message_select(
            user_msgs, display_items,
        )
        if real_idx is None:
            return False

        # 执行命令
        cmd_cls = _COMMANDS.get(action, EditCommand)
        cmd = cmd_cls(agent, real_idx)
        return cmd.execute(state)

    # ── 消息选择交互 ──

    def _interactive_message_select(
        self,
        user_msgs: list[tuple[int, dict]],
        display_items: list[str],
    ) -> int | None:
        """在底部栏补全弹窗中选择消息。

        调用前 Terminal 必须处于 cbreak 模式（monitor 运行中），
        render 线程持续驱动 ↑↓/Enter 按键处理。

        Args:
            user_msgs: [(原始索引, 消息字典), ...]。
            display_items: 每个消息的显示文本。

        Returns:
            选中的原始消息索引，None 表示取消。
        """
        bb = self._bottom_bar
        input_ = self._input
        if bb is None or input_ is None:
            return None

        sel_count = len(user_msgs)
        if sel_count == 0:
            return None

        # 显示补全弹窗（默认选中最后一条）
        try:
            bb.show_completions(
                display_items,
                sel_count - 1,
                title="\u9009\u62e9\u8981\u7f16\u8f91\u7684\u6d88\u606f",  # 选择要编辑的消息
            )
        except Exception as exc:
            _logger.debug("show_completions 失败: %s", exc)
            return None

        # 轮询等待用户选择
        last_sel_idx = sel_count - 1
        deadline = time.monotonic() + 120  # 2 分钟超时
        try:
            while time.monotonic() < deadline:
                # 检查 Enter 是否被按下
                text = input_.get_queued_input()
                if text is not None:
                    # Enter 被按下；消耗提交文本（丢弃，只需要 completion 选择）
                    break

                # 追踪当前选中的 completion 索引
                try:
                    if bb.is_completion_visible():
                        _, comp_idx, _ = bb.get_selected_completion()
                        if 0 <= comp_idx < sel_count:
                            last_sel_idx = comp_idx
                except Exception:
                    pass

                time.sleep(0.05)

            # 隐藏弹窗
            try:
                bb.hide_completions()
            except Exception:
                pass

            # 验证选择
            if last_sel_idx < 0 or last_sel_idx >= sel_count:
                return None

            return user_msgs[last_sel_idx][0]

        except Exception as exc:
            _logger.debug("_interactive_message_select 异常: %s", exc)
            try:
                bb.hide_completions()
            except Exception:
                pass
            return None


# ═══════════════════════════════════════════════════════════
# 向后兼容入口
# ═══════════════════════════════════════════════════════════

def edit_current_messages(
    agent: Any, state: dict, bottom_bar: Any = None, input_: Any = None,
    action: str = "edit",
) -> bool:
    """直接进入当前会话消息编辑（模块级入口，向后兼容）。

    Args:
        agent: ChatAgent 实例。
        state: 编辑状态字典。
        bottom_bar: _BottomBar 实例。
        input_: Input 实例。
        action: 编辑动作类型。

    Returns:
        True 表示有修改，False 表示无操作。
    """
    return MessageEditor(
        bottom_bar=bottom_bar, input_=input_,
    ).edit_current_messages(agent, state, action=action)


__all__ = [
    "MessageEditor",
    "edit_current_messages",
]
