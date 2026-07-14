"""通用 TTL 缓存工具 — 线程安全的泛型缓存

消除 command_palette / session_switcher 中重复的缓存实现。
支持泛型类型、自定义获取函数、线程安全、TTL 控制。

用法：
    cache = TTLCache(fetcher=get_registered_command_names, ttl=60.0)
    items = cache.get()       # 缓存未命中或过期时调用 fetcher
    cache.refresh()           # 强制刷新
    cache.clear()             # 清空缓存
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

_UNSET = object()


class TTLCache(Generic[T]):
    """通用 TTL 缓存 — 线程安全，泛型。

    Args:
        fetcher: 获取缓存值的函数（缓存未命中或过期时调用）。
        ttl: 缓存有效期（秒），默认 60.0。
    """

    __slots__ = ("_fetcher", "_ttl", "_value", "_time", "_lock")

    def __init__(self, fetcher: Callable[[], T], ttl: float = 60.0) -> None:
        self._fetcher = fetcher
        self._ttl = ttl
        self._value: T | object = _UNSET
        self._time: float = 0.0
        self._lock = threading.Lock()

    def get(self) -> T:
        """获取缓存值。过期或未初始化时自动调用 fetcher 刷新。

        线程安全：多个线程同时 get() 时仅一个执行 fetcher。
        """
        now = time.monotonic()
        if self._value is not _UNSET and (now - self._time) < self._ttl:
            return self._value
        return self._locked_refresh(do_double_check=True)

    def refresh(self) -> T:
        """强制刷新缓存（忽略 TTL）。"""
        return self._locked_refresh()

    def clear(self) -> None:
        """清空缓存。下次 get() 将重新加载。"""
        with self._lock:
            self._value = _UNSET
            self._time = 0.0

    # ── 内部 ──

    def _locked_refresh(self, do_double_check: bool = False) -> T:
        """带锁的刷新操作（双重检查锁定防竞态）。"""
        with self._lock:
            # 双重检查：在获取锁后确认缓存是否仍需要刷新
            if do_double_check:
                if self._value is not _UNSET and (time.monotonic() - self._time) < self._ttl:
                    return self._value
            self._value = self._fetcher()
            self._time = time.monotonic()
            return self._value


__all__ = ["TTLCache"]
