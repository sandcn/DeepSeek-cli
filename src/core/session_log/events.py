"""会话事件 — 追加到会话日志的持久事实（对应 dsh 的 SessionEvent）。

一个会话事件是不可变的 ``(type, seq, timestamp, data)`` 记录。消息、轮次、
步骤、结构化变更都以事件形式追加；模型历史由这些事件**投影**而来，而非另
行维护一份可变列表。这使「模型可见即已记录」成为可校验的运行时约束。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class SessionEvent:
    """一条不可变的会话事件。"""

    type: str
    seq: int
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SessionEvent":
        if not isinstance(raw, dict) or "type" not in raw:
            raise ValueError(f"非法会话事件: {raw!r}")
        return cls(
            type=str(raw["type"]),
            seq=int(raw.get("seq", 0)),
            timestamp=float(raw.get("timestamp", 0.0)),
            data=dict(raw.get("data", {}) or {}),
        )


def message_event_type(message: Dict[str, Any]) -> str:
    """按消息 role 映射到对应的消息事件类型。"""
    from ..events.agent_types import SessionEventType

    role = (message or {}).get("role")
    if role == "system":
        return SessionEventType.SYSTEM_MESSAGE
    if role == "user":
        return SessionEventType.USER_MESSAGE
    if role == "tool":
        return SessionEventType.TOOL_RESULT
    return SessionEventType.ASSISTANT_MESSAGE


def message_to_event_data(message: Dict[str, Any]) -> Dict[str, Any]:
    """把消息字典展开为事件 data（保留 content/reasoning/tool 字段）。"""
    data: Dict[str, Any] = {}
    for key in ("content", "reasoning_content", "tool_calls", "tool_call_id", "name"):
        if key in (message or {}):
            data[key] = message[key]
    return data


__all__ = ["SessionEvent", "message_event_type", "message_to_event_data"]
