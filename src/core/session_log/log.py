"""SessionLog — 仅追加的会话事件日志（会话的事实源）。

对应 DeepSeek Harness 的「会话日志是模型所见上下文的来源」：

- 每个事件一旦写入不再修改；消息、轮次、步骤、结构化变更都以事件追加；
- ``derive_messages()`` 从事件投影出模型历史——模型历史是**派生的**，
  不另行维护可变副本；
- ``fork()`` / ``replay()`` / ``restore()`` 让 fork、恢复、回放、遥测都从
  同一份持久事实派生。

配合 ``LoggedMessageList``（见 ``view.py``），运行时的消息视图由本日志驱动，
因此「模型可见即已记录」是可校验的运行时约束，而非约定。
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Dict, Iterable, List, Optional

from ..events.agent_types import SessionEventType
from .events import SessionEvent, message_event_type, message_to_event_data
from .projection import derive_messages


class SessionLog:
    """仅追加的会话事件日志。"""

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.session_id = session_id
        self._events: List[SessionEvent] = []
        self._seq = itertools.count(1)

    # ── 追加 ─────────────────────────────────────────────

    def append(self, event_type: str, **data: Any) -> SessionEvent:
        """追加一条事件（唯一写入入口）。"""
        event = SessionEvent(
            type=event_type,
            seq=next(self._seq),
            timestamp=time.time(),
            data=dict(data),
        )
        self._events.append(event)
        return event

    def append_message(self, message: Dict[str, Any], *, index: Optional[int] = None) -> SessionEvent:
        """按消息 role 追加对应的消息事件（可选插入位置）。"""
        event_type = message_event_type(message)
        data = message_to_event_data(message)
        if index is not None:
            data["index"] = int(index)
            return self.append(SessionEventType.INSERT, index=int(index), message=dict(message))
        return self.append(event_type, **data)

    # ── 结构变更 ─────────────────────────────────────────

    def insert(self, index: int, message: Dict[str, Any]) -> SessionEvent:
        return self.append(SessionEventType.INSERT, index=int(index), message=dict(message))

    def replace(self, index: int, message: Dict[str, Any]) -> SessionEvent:
        return self.append(SessionEventType.REPLACE, index=int(index), message=dict(message))

    def delete(self, start: int, stop: int) -> SessionEvent:
        return self.append(SessionEventType.DELETE, start=int(start), stop=int(stop))

    def truncate(self, length: int) -> SessionEvent:
        return self.append(SessionEventType.TRUNCATE, length=int(length))

    def reset(self) -> SessionEvent:
        return self.append(SessionEventType.RESET)

    # ── 读取 / 投影 ──────────────────────────────────────

    def events(self) -> List[SessionEvent]:
        return list(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def derive_messages(self, projections=None) -> List[Dict[str, Any]]:
        """从事件投影出模型历史（「模型可见即已记录」的来源）。"""
        return derive_messages(self._events, projections)

    def replay(self, projections=None) -> List[Dict[str, Any]]:
        """回放：等价于从零重新投影。"""
        return self.derive_messages(projections)

    def message_count(self) -> int:
        return len(self.derive_messages())

    def verify(self, messages: Iterable[Dict[str, Any]], projections=None) -> bool:
        """校验给定消息列表与日志投影一致（「模型可见即已记录」）。"""
        return list(messages) == self.derive_messages(projections)

    # ── fork / 恢复 ──────────────────────────────────────

    def fork(self, at: Optional[int] = None, *, session_id: Optional[str] = None) -> "SessionLog":
        """在 ``at`` 处派生一个新日志（默认全量），用于会话 fork。"""
        events = self._events if at is None else self._events[: max(0, int(at))]
        return SessionLog.restore(
            [event.to_dict() for event in events],
            session_id=session_id if session_id is not None else self.session_id,
        )

    def snapshot(self) -> List[Dict[str, Any]]:
        """序列化事件列表（持久化用）。"""
        return [event.to_dict() for event in self._events]

    @classmethod
    def restore(cls, raw_events: Iterable[Dict[str, Any]], *, session_id: Optional[str] = None) -> "SessionLog":
        """从序列化的事件列表重建日志（恢复/回放）。"""
        log = cls(session_id=session_id)
        max_seq = 0
        for raw in raw_events or []:
            event = SessionEvent.from_dict(raw)
            log._events.append(event)
            max_seq = max(max_seq, event.seq)
        log._seq = itertools.count(max_seq + 1)
        return log

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<SessionLog events={len(self._events)} messages={self.message_count()}>"


__all__ = ["SessionLog"]
