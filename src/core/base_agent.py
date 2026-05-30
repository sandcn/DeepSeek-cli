"""Agent 基类 — 纯消息管理

提供公共的消息追加和沙盒同步方法。
保持核心逻辑简单，工具调用等复杂行为由 Agent 子类实现。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .sandbox_manager import get_sandbox_manager, set_current_message_index

_logger = logging.getLogger(__name__)


def _serialize_tool_arguments(arguments: Any) -> str:
    """安全序列化 tool_call arguments 为 JSON 字符串。

    处理 None/str/dict 三种输入类型，异常时回退为错误描述 JSON。
    """
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        _logger.warning("工具参数序列化失败: %s", e)
        return json.dumps({"error": "序列化失败", "raw": str(arguments)[:500]}, ensure_ascii=False)


def _build_tool_calls_payload(tool_calls: list[dict]) -> list[dict]:
    """将原始 tool_calls 转换为 API 兼容格式。

    为每个 tool_call 添加 type="function" 和嵌套的 function 结构。
    """
    processed = []
    for tc in tool_calls:
        processed.append({
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": _serialize_tool_arguments(tc.get("arguments")),
            },
        })
    return processed


# 非ABC：为 Agent/SubAgent 提供共享消息操作
class BaseAgent:
    """消息管理基类（非ABC）——为 Agent/SubAgent 提供 add_user_message/_append_tool_result 等共享的消息操作。注意：这不是抽象基类，不含抽象方法。"""

    def __init__(self):
        self.messages: list[dict] = []
        self.model: str | None = None
        self.tools: list[dict] = []

    # ── 沙盒索引同步 ──────────────────────────────────

    def _sync_sandbox_index(self, msg_index: int | None = None) -> None:
        """同步沙盒管理器的消息索引到指定消息位置。

        SubAgent 设置 _skip_sandbox_update=True 阻止此更新，
        避免多个并发 SubAgent 的 thread local 互相覆盖。

        Args:
            msg_index: 目标消息索引。为 None 时取当前消息列表末尾。
        """
        if getattr(self, '_skip_sandbox_update', False):
            return
        sandbox_manager = get_sandbox_manager()
        if not sandbox_manager:
            return
        idx = msg_index if msg_index is not None else (len(self.messages) - 1 if self.messages else 0)
        sandbox_manager.update_message_index(idx)
        set_current_message_index(idx)

    # ── 消息管理 ──────────────────────────────────────

    def add_user_message(self, content: str | None) -> None:
        """添加用户消息到消息列表。"""
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        self.messages.append({"role": "user", "content": content})

    def _append_assistant_message(
        self,
        content: str | None,
        tool_calls: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        """追加 assistant 消息。

        处理 DeepSeek thinking mode 的强制约束：
        - tool_calls 存在时 content 必须为 None
        - reasoning_content key 始终存在（即使为空字符串）
        """
        msg: dict[str, Any] = {"role": "assistant"}

        # content: tool_calls 时置 None，否则保底空字符串
        msg["content"] = None if tool_calls else (content or "")

        # reasoning_content: DeepSeek 要求 key 始终存在
        msg["reasoning_content"] = (
            reasoning_content if isinstance(reasoning_content, str)
            else _logger.debug("reasoning_content 类型异常 (%s)，回退为空", type(reasoning_content).__name__) or ""
        )

        if tool_calls:
            msg["tool_calls"] = _build_tool_calls_payload(tool_calls)

        self.messages.append(msg)
        self._sync_sandbox_index(len(self.messages) - 1)

    def _append_tool_result(self, tool_call_id: str, content: str) -> None:
        """追加 tool 角色消息。"""
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._sync_sandbox_index(len(self.messages) - 1)
