"""线程安全集合管理 — ThreadSafeList 线程安全列表封装。

提供基于 threading.Lock 的线程安全列表，用于统一管理
ChatRenderState 的 captured_*_output 等可变集合。

设计决策（v2026-07-26）：
  - 仅封装 list 的基础可变操作（append/clear/__iter__/__len__/__getitem__）
  - 不引入 dirty/version 模式（AgentStateStore 的 dirty/version 有特定用途，
    与简单列表管理差异过大，不适合统一）
  - 最小化侵入：保持 list 兼容接口，下游代码无需修改
"""

from __future__ import annotations

import threading
from typing import Any, Iterator, List, TypeVar

T = TypeVar("T")


class ThreadSafeList:
    """线程安全列表封装，提供 list 兼容接口。

    使用 threading.Lock 保护所有写操作和读操作，
    确保多线程环境下的 append/clear/__len__/__iter__ 等操作安全。

    主要用途：
      - ChatRenderState 的 captured_reasoning_output / captured_content_output

    兼容性：
      - 支持 append()、clear()、__iter__()、__len__()、__getitem__()
      - 支持 "".join() 操作（通过 __iter__）
      - 支持 bool() 判断（通过 __bool__ → __len__）
      - 支持 getattr(obj, attr, None) 的安全检测

    设计模式：代理 — 对 list 操作添加线程安全代理层
    """

    def __init__(self, initial: list[T] | None = None) -> None:
        self._data: list[T] = list(initial) if initial else []
        self._lock = threading.Lock()

    def append(self, item: T) -> None:
        """线程安全追加元素。"""
        with self._lock:
            self._data.append(item)

    def clear(self) -> None:
        """线程安全清空列表。"""
        with self._lock:
            self._data.clear()

    def extend(self, items: list[T]) -> None:
        """线程安全批量追加。"""
        with self._lock:
            self._data.extend(items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __getitem__(self, index: int) -> T:
        with self._lock:
            return self._data[index]

    def __iter__(self) -> Iterator[T]:
        with self._lock:
            return iter(list(self._data))

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._data)

    def __repr__(self) -> str:
        with self._lock:
            return repr(self._data)

    def to_list(self) -> list[T]:
        """返回当前数据的快照列表（线程安全）。"""
        with self._lock:
            return list(self._data)
