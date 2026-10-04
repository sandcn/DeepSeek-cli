"""LoggedMessageList — 由会话日志驱动的消息视图（会话事实源在视图侧）。

运行时对消息列表的一切读写都落到 ``SessionLog``：

- 读取：命中派生缓存（``_items``），O(1)；
- 写入：先更新缓存，再向日志追加对应事件（追加 / 插入 / 替换 / 删除 /
  截断 / 重置），保持「日志是唯一事实源」与「模型可见即已记录」。

视图实现完整 ``MutableSequence`` 语义（含切片赋值），与 ``list`` 可互换，
现有直接操作 ``agent.messages`` 的代码无需改动。
"""

from __future__ import annotations

from collections.abc import MutableSequence
from typing import Any, Dict, Iterable, Iterator, List, Optional, Union, overload

from .log import SessionLog


class LoggedMessageList(MutableSequence):
    """由 ``SessionLog`` 驱动的消息列表视图。"""

    def __init__(self, log: Optional[SessionLog] = None, initial: Optional[Iterable[Dict[str, Any]]] = None) -> None:
        self._log = log if log is not None else SessionLog()
        self._items: List[Dict[str, Any]] = []
        if initial:
            for message in initial:
                self._log.append_message(dict(message))
        self._items = self._log.derive_messages()

    # ── 事实源访问 ───────────────────────────────────────

    @property
    def log(self) -> SessionLog:
        return self._log

    def rebind(self, log: SessionLog) -> None:
        """切换事实源（恢复 / fork / 回放）并重新派生缓存。"""
        self._log = log
        self.reload()

    def reload(self) -> List[Dict[str, Any]]:
        """从日志重新派生缓存（日志被外部重建后调用）。"""
        self._items = self._log.derive_messages()
        return self._items

    def verify(self, projections=None) -> bool:
        """校验缓存与日志投影一致（「模型可见即已记录」运行时约束）。"""
        return self._items == self._log.derive_messages(projections)

    # ── 读取 ─────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._items)

    @overload
    def __getitem__(self, index: int) -> Dict[str, Any]: ...

    @overload
    def __getitem__(self, index: slice) -> List[Dict[str, Any]]: ...

    def __getitem__(self, index):
        return self._items[index]

    def __iter__(self) -> Iterator[Dict[str, Any]]:
        return iter(self._items)

    def __contains__(self, value: object) -> bool:
        return value in self._items

    def index(self, value, start: int = 0, stop: Optional[int] = None):  # type: ignore[override]
        if stop is None:
            return self._items.index(value, start)
        return self._items.index(value, start, stop)

    def count(self, value) -> int:
        return self._items.count(value)

    # ── 写入 ─────────────────────────────────────────────

    def __setitem__(self, index, value) -> None:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self._items))
            if step != 1:
                for offset, item in zip(range(start, stop, step), value):
                    self.__setitem__(offset, item)
                return
            replacement = list(value)
            if stop > start:
                self._log.delete(start, stop)
            for offset, item in enumerate(replacement):
                self._log.insert(start + offset, item)
            self._items[index] = replacement
            return
        self._log.replace(index, value)
        self._items[index] = value

    def __delitem__(self, index) -> None:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self._items))
            if step != 1:
                for offset in sorted(range(start, stop, step), reverse=True):
                    self.__delitem__(offset)
                return
            if stop > start:
                self._log.delete(start, stop)
            del self._items[index]
            return
        length = len(self._items)
        resolved = index if index >= 0 else length + index
        if resolved < 0 or resolved >= length:
            raise IndexError("list assignment index out of range")
        self._log.delete(resolved, resolved + 1)
        del self._items[index]

    def insert(self, index: int, value: Dict[str, Any]) -> None:
        self._log.insert(index, value)
        self._items.insert(index, value)

    def append(self, value: Dict[str, Any]) -> None:
        # 末尾追加走消息事件（更紧凑，回放等价）
        self._log.append_message(value)
        self._items.append(value)

    def extend(self, values: Iterable[Dict[str, Any]]) -> None:
        for value in values:
            self.append(value)

    def pop(self, index: int = -1) -> Dict[str, Any]:
        length = len(self._items)
        resolved = index if index >= 0 else length + index
        if resolved < 0 or resolved >= length:
            raise IndexError("pop index out of range")
        value = self._items.pop(index)
        self._log.delete(resolved, resolved + 1)
        return value

    def remove(self, value: Dict[str, Any]) -> None:
        self.pop(self._items.index(value))

    def clear(self) -> None:
        self._log.reset()
        self._items = [m for m in self._items if m.get("role") == "system"]

    def reverse(self) -> None:
        # 结构变更按「先清空再按新顺序插入」记录，保持日志为唯一事实源
        ordered = list(reversed(self._items))
        self._log.reset()
        self._log.delete(0, len(self._items))
        for offset, item in enumerate(ordered):
            self._log.insert(offset, item)
        self._items = ordered

    # ── 兼容 ─────────────────────────────────────────────

    def copy(self) -> List[Dict[str, Any]]:
        return list(self._items)

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LoggedMessageList):
            return self._items == other._items
        if isinstance(other, list):
            return self._items == other
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return repr(self._items)


__all__ = ["LoggedMessageList"]
