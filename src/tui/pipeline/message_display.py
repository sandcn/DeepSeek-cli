"""消息显示函数 — 精简版消息格式化与显示。

提供 MessageDisplayContext（消息上下文封装）、display_messages（消息列表显示）、
RoleConfig（角色图标配置）。
"""

from __future__ import annotations

import sys
from src._compat import dataclass
from typing import Any

from ...core.constants import DIM, RESET, CYAN, YELLOW, GREEN


@dataclass
class RoleConfig:
    """角色显示配置。"""
    icon: str = "?"


_DEFAULT_ROLE_MAP: dict[str, RoleConfig] = {
    "user": RoleConfig(icon="\u25cf"),       # ●
    "assistant": RoleConfig(icon="\u25c6"),  # ◆
    "tool": RoleConfig(icon="\u2699"),       # ⚙
}


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
    """截断文本到指定长度。"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


@dataclass
class MessageDisplayContext:
    """消息显示上下文 — 封装消息列表及其元数据。"""

    data: list[dict]
    """非 system 消息列表（副本，不含 system 消息）。"""
    agent: Any = None
    """ChatAgent 实例（可选）。"""
    idx_map: list[int] = None  # type: ignore[assignment]
    """索引映射：data[i] 在原 messages 中的位置。"""

    def __post_init__(self):
        if self.idx_map is None:
            self.idx_map = list(range(len(self.data)))

    @classmethod
    def from_messages(cls, messages: list[dict]) -> MessageDisplayContext:
        """从消息列表创建上下文。"""
        data = [m for m in messages if m.get("role") != "system"]
        return cls(data=data, idx_map=None)

    @classmethod
    def from_agent(cls, agent: Any) -> MessageDisplayContext:
        """从 agent 创建上下文。"""
        messages = getattr(agent, 'messages', [])
        data = [m for m in messages if m.get("role") != "system"]
        idx_map = []
        for i, m in enumerate(messages):
            if m.get("role") != "system":
                idx_map.append(i)
        return cls(data=data, agent=agent, idx_map=idx_map)


def display_messages(
    data: list[dict],
    agent: Any = None,
    idx_map: list[int] | None = None,
    speed: int = 0,
) -> None:
    """将消息列表渲染到终端（简化版 — 直接写入 stdout）。

    pipeline/message_display.py 已删除（TUI 重构），此为精简内置替代。
    保留与旧版的接口兼容。

    Args:
        data: 消息列表（不含 system 消息）。
        agent: ChatAgent 实例（可选）。
        idx_map: 索引映射（可选）。
        speed: 保留参数（兼容旧接口，当前忽略）。
    """
    for msg in data:
        role = msg.get("role", "")
        cfg = _DEFAULT_ROLE_MAP.get(role)
        icon = cfg.icon if cfg else "\u00b7"
        content = _content_str(msg.get("content", ""))
        if not content.strip():
            continue
        # 截断过长的消息用于显示
        preview = _truncate(content.replace('\n', ' '), 120)
        sys.__stdout__.write(f"  {icon} [{role}] {preview}\n")
    sys.__stdout__.flush()


__all__ = [
    "MessageDisplayContext",
    "RoleConfig",
    "display_messages",
]
