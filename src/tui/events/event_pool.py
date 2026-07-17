"""高频事件对象池 — 减少 ContentChunkEvent/ReasoningChunkEvent/ToolOutputChunkEvent 的分配开销。

使用 collections.deque 作为底层存储，支持线程安全的 acquire/release。
保留事件类型的 frozen dataclass 不变，通过 object.__new__ 绕过 __init__ 创建空白实例，
再手动设置字段值（object.__setattr__）。

用法：
    pool = EventPool(maxsize=256)
    event = pool.acquire(ContentChunkEvent, text="hello", label="agent-1")
    # ... 使用 event（发布到 EventBus 等）...
    pool.release(event)  # 重置字段后放回池中，供下次复用
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Dict, Optional, Type, TypeVar

from .event_types import (
    ContentChunkEvent,
    DisplayEvent,
    ReasoningChunkEvent,
    ToolOutputChunkEvent,
)

T = TypeVar("T", bound=DisplayEvent)

# 高频事件类型集合 — 此集合内的事件使用对象池管理
_HIGH_FREQ_TYPES: frozenset = frozenset({
    ContentChunkEvent,
    ReasoningChunkEvent,
    ToolOutputChunkEvent,
})

# 各事件类型对应的字段默认值（用于 release 时重置）
# 所有三个高频事件共有的字段：timestamp, source, text, label
_DEFAULT_FIELD_VALUES: dict[str, Any] = {
    "timestamp": 0.0,  # acquire 时会重新设为 time.time()
    "source": "",
    "text": "",
    "label": "",
}

# 全部三个高频事件类型共有的字段名集合
_ALL_FIELDS = frozenset({"timestamp", "source", "text", "label"})


class EventPool:
    """高频事件对象池 — 减少 GC 压力和内存分配开销。

    支持的池化事件类型：ContentChunkEvent, ReasoningChunkEvent, ToolOutputChunkEvent。
    线程安全（threading.Lock 保护）。

    Attributes:
        maxsize: 每类事件的最大池容量，默认 256
    """

    def __init__(self, maxsize: int = 256) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize 必须 ≥ 1，收到 {maxsize}")
        self._maxsize = maxsize
        # 按事件类型分池存储：{type: deque}
        self._pools: dict[type, deque] = {
            ContentChunkEvent: deque(),
            ReasoningChunkEvent: deque(),
            ToolOutputChunkEvent: deque(),
        }
        self._lock = threading.Lock()

    def acquire(self, event_type: type[T], **kwargs: Any) -> T:
        """从对象池获取一个事件实例。

        优先从池中取空闲对象（重置字段后返回），
        池空时使用 object.__new__ 创建新实例（绕过 frozen __init__）。

        Args:
            event_type: 事件类型（ContentChunkEvent / ReasoningChunkEvent / ToolOutputChunkEvent）
            **kwargs: 事件字段值（text, label, source 等）。timestamp 省略时自动设为 time.time()。

        Returns:
            事件实例，字段值已按 kwargs 设置

        Raises:
            TypeError: event_type 不是 DisplayEvent 子类
        """
        if event_type not in _HIGH_FREQ_TYPES:
            # 非高频类型：直接创建新实例（不池化）
            return event_type(**kwargs)

        with self._lock:
            pool = self._pools.get(event_type)
            if pool and len(pool) > 0:
                instance = pool.popleft()
            else:
                # 池空：通过 object.__new__ 创建空白实例（绕过 frozen dataclass __init__）
                instance = object.__new__(event_type)

        # 设置字段值（frozen dataclass 需用 object.__setattr__）
        # 先设置所有字段为默认值，再用 kwargs 覆盖
        for field_name in _ALL_FIELDS:
            if field_name in kwargs:
                object.__setattr__(instance, field_name, kwargs[field_name])
            elif field_name == "timestamp":
                object.__setattr__(instance, field_name, time.time())
            else:
                object.__setattr__(instance, field_name, _DEFAULT_FIELD_VALUES[field_name])

        return instance

    def release(self, event: DisplayEvent) -> None:
        """将事件实例放回对象池。

        重置所有字段为默认值后放回池中。
        池满时静默丢弃（由 GC 回收），不抛出异常。

        Args:
            event: 要回收的事件实例
        """
        event_type = type(event)
        if event_type not in _HIGH_FREQ_TYPES:
            return  # 非高频类型不池化

        with self._lock:
            pool = self._pools.get(event_type)
            if pool is None:
                return
            if len(pool) >= self._maxsize:
                return  # 池满，静默丢弃

            # 重置所有字段为默认值
            for field_name, default_val in _DEFAULT_FIELD_VALUES.items():
                object.__setattr__(event, field_name, default_val)

            pool.append(event)

    @property
    def maxsize(self) -> int:
        """获取每类事件的池最大容量。"""
        return self._maxsize

    def qsize(self, event_type: Optional[type[DisplayEvent]] = None) -> int:
        """获取池中空闲对象数量。

        Args:
            event_type: 指定事件类型，None 时返回所有类型的总数

        Returns:
            空闲对象数量
        """
        with self._lock:
            if event_type is not None:
                pool = self._pools.get(event_type)
                return len(pool) if pool else 0
            return sum(len(p) for p in self._pools.values())

    def clear(self, event_type: Optional[type[DisplayEvent]] = None) -> None:
        """清空对象池。

        Args:
            event_type: 指定事件类型，None 时清空所有类型
        """
        with self._lock:
            if event_type is not None:
                pool = self._pools.get(event_type)
                if pool:
                    pool.clear()
            else:
                for pool in self._pools.values():
                    pool.clear()
