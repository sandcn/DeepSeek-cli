"""会话投影 — 从仅追加的事件日志派生模型历史与附加状态。

两个层次：

1. ``derive_messages(events, projections=None)``：把会话事件**投影**为模型
   历史（``list[dict]``）。这是「会话日志是模型所见上下文的来源」的实现；
   ``projections`` 是纯消息投影（``(message) -> message``），供修改现有
   消息内容的插件注册，独立读取器显式传入相同的处理器。
2. ``ProjectionRegistry``（对应 dsh 的 ``ctx.sessionProjections``）：已注册
   单元增量折叠已提交事件，host 消费方通过 ``state_of()`` 读取单个类型化
   状态，载体通过 ``snapshot()`` 批量取得裁剪后的客户端视图。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Optional

from ..events.agent_types import SessionEventType

MessageProjection = Callable[[Dict[str, Any]], Dict[str, Any]]
StateFolder = Callable[[Any, Any], Any]


def _message_from_event(event: Any) -> Optional[Dict[str, Any]]:
    """把消息类事件展开为模型历史中的一条消息（非消息事件返回 None）。"""
    data = event.data or {}
    kind = event.type
    if kind == SessionEventType.SYSTEM_MESSAGE:
        return {"role": "system", "content": data.get("content", "")}
    if kind == SessionEventType.USER_MESSAGE:
        return {"role": "user", "content": data.get("content", "")}
    if kind == SessionEventType.ASSISTANT_MESSAGE:
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": data.get("content", ""),
        }
        if data.get("reasoning_content") is not None:
            message["reasoning_content"] = data["reasoning_content"]
        if data.get("tool_calls"):
            message["tool_calls"] = data["tool_calls"]
        return message
    if kind == SessionEventType.TOOL_RESULT:
        return {
            "role": "tool",
            "content": data.get("content", ""),
            "tool_call_id": data.get("tool_call_id", ""),
        }
    return None


def _apply_projection(message: Dict[str, Any], projections: Optional[Iterable[MessageProjection]]) -> Dict[str, Any]:
    for projector in projections or ():
        message = projector(message)
    return message


def derive_messages(
    events: Iterable[Any],
    projections: Optional[Iterable[MessageProjection]] = None,
) -> List[Dict[str, Any]]:
    """把仅追加的会话事件投影为模型历史。"""
    messages: List[Dict[str, Any]] = []
    for event in events:
        message = _message_from_event(event)
        if message is not None:
            messages.append(_apply_projection(message, projections))
            continue
        kind = event.type
        data = event.data or {}
        if kind == SessionEventType.INSERT:
            index = int(data.get("index", len(messages)))
            item = data.get("message")
            if isinstance(item, dict):
                messages.insert(index, _apply_projection(dict(item), projections))
        elif kind == SessionEventType.REPLACE:
            index = int(data.get("index", -1))
            item = data.get("message")
            if isinstance(item, dict) and 0 <= index < len(messages):
                messages[index] = _apply_projection(dict(item), projections)
        elif kind == SessionEventType.DELETE:
            start = int(data.get("start", 0))
            stop = int(data.get("stop", start))
            del messages[start:stop]
        elif kind == SessionEventType.TRUNCATE:
            length = int(data.get("length", 0))
            del messages[length:]
        elif kind == SessionEventType.RESET:
            messages = [m for m in messages if m.get("role") == "system"]
    return messages


# ═══════════════════════════════════════════════════════════════
# ProjectionRegistry（ctx.sessionProjections 的实现）
# ═══════════════════════════════════════════════════════════════


class _RegisteredProjection:
    __slots__ = ("name", "initial", "folder")

    def __init__(self, name: str, initial: Callable[[], Any], folder: StateFolder) -> None:
        self.name = name
        self.initial = initial
        self.folder = folder


class ProjectionRegistry:
    """已注册投影单元 — 增量折叠已提交事件。"""

    def __init__(self) -> None:
        self._projections: Dict[str, _RegisteredProjection] = {}
        self._state: Dict[str, Any] = {}
        self._cursor: Dict[str, int] = {}

    def register(
        self,
        name: str,
        folder: StateFolder,
        *,
        initial: Optional[Callable[[], Any]] = None,
    ) -> Callable[[], None]:
        """注册一个投影单元，返回注销函数。"""
        if not isinstance(name, str) or not name:
            raise ValueError(f"投影名必须是非空字符串: {name!r}")
        if not callable(folder):
            raise TypeError(f"投影折叠函数必须可调用: {folder!r}")
        self._projections[name] = _RegisteredProjection(
            name, initial or (lambda: None), folder
        )
        self._state.pop(name, None)
        self._cursor.pop(name, None)
        registered = True

        def _dispose() -> None:
            nonlocal registered
            if not registered:
                return
            registered = False
            self._projections.pop(name, None)
            self._state.pop(name, None)
            self._cursor.pop(name, None)

        return _dispose

    def unregister(self, name: str) -> bool:
        if name not in self._projections:
            return False
        self._projections.pop(name, None)
        self._state.pop(name, None)
        self._cursor.pop(name, None)
        return True

    def names(self) -> List[str]:
        return sorted(self._projections)

    def has(self, name: str) -> bool:
        return name in self._projections

    def _fold(self, name: str, events: List[Any]) -> Any:
        projection = self._projections[name]
        if name not in self._state:
            self._state[name] = projection.initial()
            self._cursor[name] = 0
        state = self._state[name]
        cursor = self._cursor.get(name, 0)
        for event in events[cursor:]:
            state = projection.folder(state, event)
        self._cursor[name] = len(events)
        self._state[name] = state
        return state

    def state_of(self, name: str, events: List[Any]) -> Any:
        """读取单个类型化状态的当前值（增量折叠）。"""
        if name not in self._projections:
            raise KeyError(f"未注册的投影: {name!r}")
        return self._fold(name, events)

    def snapshot(self, events: List[Any]) -> Dict[str, Any]:
        """批量取得全部投影的当前状态（裁剪后的客户端视图）。"""
        return {name: self._fold(name, events) for name in self.names()}

    def reset(self) -> None:
        self._state.clear()
        self._cursor.clear()

    def clear(self) -> None:
        self._projections.clear()
        self.reset()


__all__ = ["derive_messages", "ProjectionRegistry", "MessageProjection", "StateFolder"]
