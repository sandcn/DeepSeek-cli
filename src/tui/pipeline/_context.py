"""纯数据层 — message_display 的上下文与沙盒数据函数。

包含 _scroll_window、沙盒格式化/查询函数、_non_system_messages
过滤函数和 MessageDisplayContext 数据类。
"""

from __future__ import annotations

from typing import Any
import os

from src._compat import dataclass
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox_manager


def _scroll_window(cursor: int, state: dict, total: int) -> tuple[int, int]:
    """计算可见窗口 [start, end)。"""
    max_visible = state.get("max", 15)
    if total <= max_visible:
        return 0, total
    offset = state.get("scroll", 0)
    if cursor < offset:
        offset = cursor
    elif cursor >= offset + max_visible:
        offset = cursor - max_visible + 1
    state["scroll"] = offset
    return offset, min(offset + max_visible, total)


def _format_sandbox_text(sandbox_info: dict | None) -> str:
    """格式化沙盒信息为显示文本。"""
    if not sandbox_info or sandbox_info.get("count", 0) == 0:
        return ""
    changes = sandbox_info.get("file_changes", [])
    count = sandbox_info.get("count", 0)
    parts = []
    for fc in changes:
        name = os.path.basename(fc["file_path"])
        ctype = fc["change_type"]
        parts.append(f"{name}({ctype})")
    return f" [沙盒: 改变了{count}个文件: " + ", ".join(parts) + "]"


def _get_sandbox_text(agent: Any, idx_map: list[int] | None, data_idx: int) -> str:
    """获取沙盒信息文本。"""
    if not agent or not idx_map or data_idx >= len(idx_map):
        return ""
    sandbox_manager = _get_sandbox_manager()
    if not sandbox_manager:
        return ""
    real_idx = idx_map[data_idx]
    info = sandbox_manager.get_sandbox_info(real_idx) or {}
    return _format_sandbox_text(info)


def _get_user_sandbox_text(
    data: list[dict], data_idx: int, agent: Any, idx_map: list[int] | None,
) -> str:
    """对于 user 消息，查找其后最近的 assistant(tool_calls) 的沙盒信息。"""
    if not agent or not idx_map:
        return ""
    for j in range(data_idx + 1, len(data)):
        m = data[j]
        if m.get("role") == "user":
            break
        if m.get("tool_calls"):
            return _get_sandbox_text(agent, idx_map, j)
    return ""


def _non_system_messages(messages: list[dict]) -> tuple[list[dict], list[int]]:
    """过滤 system 消息，返回 (data, idx_map)。

    data = messages 中 role != "system" 的消息列表，
    idx_map 是 data 索引到 messages 全量索引的映射。
    """
    data: list[dict] = []
    idx_map: list[int] = []
    for i, m in enumerate(messages):
        if m.get("role") != "system":
            data.append(m)
            idx_map.append(i)
    return data, idx_map


@dataclass(slots=True)
class MessageDisplayContext:
    """消息显示上下文 — 封装 agent 相关的三个强关联参数。

    消除 _msg_line / _make_message_lines 中反复传递
    agent / idx_map / data 三个参数的模式。

    data = messages 过滤 system 后的结果，
    idx_map 是 messages 全量到 data 的索引映射。

    用法：
        ctx = MessageDisplayContext.from_messages(agent.messages)
        # 或直接使用 agent：
        ctx = MessageDisplayContext.from_agent(agent)
        _msg_line(msg, i, ctx)
        _make_message_lines(items, cursor, state, ctx, title, tag, is_current)
    """
    data: list[dict]
    agent: Any = None
    idx_map: list[int] | None = None

    @classmethod
    def from_messages(cls, messages: list[dict], agent: Any = None) -> "MessageDisplayContext":
        """从消息列表构建上下文（提取 data + idx_map）。

        Args:
            messages: 完整消息列表（含 system 消息）。
            agent: 可选的 agent 引用（用于沙盒查询等）。

        Returns:
            构建好的 MessageDisplayContext。
        """
        data, idx_map = _non_system_messages(messages)
        return cls(data=data, agent=agent, idx_map=idx_map)

    @classmethod
    def from_agent(cls, agent: Any) -> "MessageDisplayContext":
        """从 agent 自动构建上下文（提取 data + idx_map）。

        等价于 MessageDisplayContext.from_messages(agent.messages, agent=agent)。
        """
        if agent is None:
            return cls(data=[])
        return cls.from_messages(agent.messages, agent=agent)


__all__ = [
    "_scroll_window",
    "_format_sandbox_text",
    "_get_sandbox_text",
    "_get_user_sandbox_text",
    "_non_system_messages",
    "MessageDisplayContext",
]
