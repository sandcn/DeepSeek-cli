"""LoggedMessageList — 由会话日志驱动的消息视图（会话事实源在视图侧）。

运行时对消息列表的一切读写都落到 ``SessionLog``：

- 读取：直接走 ``list`` 存储（本类即 ``list`` 子类，O(1)）；
- 写入：先更新 ``list`` 存储，再向日志追加对应事件（追加 / 插入 / 替换 /
  删除 / 截断 / 重置），保持「日志是唯一事实源」与「模型可见即已记录」。

★ 本类继承 ``list``（而非 `collections.abc.MutableSequence`），以真正实现
「与 list 可互换」契约：``isinstance(messages, list)`` 为真、
``json.dumps(messages)`` 可直接序列化、切片/拼接/排序等 list 语义原生可用。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .log import SessionLog


class LoggedMessageList(list):
    """由 ``SessionLog`` 驱动的消息列表视图（``list`` 子类）。"""

    def __init__(
        self,
        log: Optional[SessionLog] = None,
        initial: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        super().__init__()
        self._log = log if log is not None else SessionLog()
        if initial:
            for message in initial:
                self._log.append_message(dict(message))
        super().extend(self._log.derive_messages())

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
        derived = self._log.derive_messages()
        list.clear(self)
        list.extend(self, derived)
        return derived

    def verify(self, projections=None) -> bool:
        """校验缓存与日志投影一致（「模型可见即已记录」运行时约束）。"""
        return list(self) == self._log.derive_messages(projections)

    # ── 内部：日志重同步 ─────────────────────────────────

    def _resync_log(self) -> None:
        """按当前 list 内容重建日志（结构变更无法逐条映射时使用）。"""
        self._log.reset()
        self._log.delete(0, len(self))
        for offset, item in enumerate(list(self)):
            self._log.insert(offset, item)

    # ── 写入（同步日志） ─────────────────────────────────

    def append(self, value: Dict[str, Any]) -> None:
        # 末尾追加走消息事件（更紧凑，回放等价）
        self._log.append_message(value)
        list.append(self, value)

    def insert(self, index: int, value: Dict[str, Any]) -> None:
        self._log.insert(index, value)
        list.insert(self, index, value)

    def extend(self, values: Iterable[Dict[str, Any]]) -> None:
        for value in values:
            self.append(value)

    def __iadd__(self, values: Iterable[Dict[str, Any]]):
        self.extend(values)
        return self

    def __imul__(self, n: int):
        list.__imul__(self, n)
        self._resync_log()
        return self

    def __setitem__(self, index, value) -> None:
        length = len(self)
        if isinstance(index, slice):
            start, stop, step = index.indices(length)
            if step != 1:
                for offset, item in zip(range(start, stop, step), value):
                    self.__setitem__(offset, item)
                return
            replacement = list(value)
            if stop > start:
                self._log.delete(start, stop)
            for offset, item in enumerate(replacement):
                self._log.insert(start + offset, item)
            list.__setitem__(self, index, replacement)
            return
        resolved = index if index >= 0 else length + index
        if resolved < 0 or resolved >= length:
            raise IndexError("list assignment index out of range")
        list.__setitem__(self, index, value)
        self._log.replace(resolved, value)

    def __delitem__(self, index) -> None:
        length = len(self)
        if isinstance(index, slice):
            start, stop, step = index.indices(length)
            if step != 1:
                for offset in sorted(range(start, stop, step), reverse=True):
                    self.__delitem__(offset)
                return
            if stop > start:
                self._log.delete(start, stop)
            list.__delitem__(self, index)
            return
        resolved = index if index >= 0 else length + index
        if resolved < 0 or resolved >= length:
            raise IndexError("list assignment index out of range")
        list.__delitem__(self, index)
        self._log.delete(resolved, resolved + 1)

    def pop(self, index: int = -1) -> Dict[str, Any]:
        length = len(self)
        resolved = index if index >= 0 else length + index
        if resolved < 0 or resolved >= length:
            raise IndexError("pop index out of range")
        value = list.pop(self, index)
        self._log.delete(resolved, resolved + 1)
        return value

    def remove(self, value: Dict[str, Any]) -> None:
        idx = list.index(self, value)
        list.__delitem__(self, idx)
        self._log.delete(idx, idx + 1)

    def clear(self) -> None:
        self._log.reset()
        kept = [m for m in list(self) if isinstance(m, dict) and m.get("role") == "system"]
        list.clear(self)
        list.extend(self, kept)

    def reverse(self) -> None:
        list.reverse(self)
        self._resync_log()

    def sort(self, *args, **kwargs) -> None:
        list.sort(self, *args, **kwargs)
        self._resync_log()

    # ── 兼容 ─────────────────────────────────────────────

    def copy(self) -> List[Dict[str, Any]]:
        return list(self)

    def to_list(self) -> List[Dict[str, Any]]:
        return list(self)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return list.__repr__(self)


__all__ = ["LoggedMessageList"]
