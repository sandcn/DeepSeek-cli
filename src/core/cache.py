"""通用缓存层 — CachePort + 内置实现

提供可插拔的缓存抽象，支持内存 LRU 缓存和空缓存。
核心模块通过 CachePort 接口使用缓存，不直接依赖具体实现。

使用方式:
    from .cache import get_default_cache

    cache = get_default_cache()
    cache.set("my_key", {"data": 42}, ttl=300)
    value = cache.get("my_key")  # → {"data": 42}
"""

from __future__ import annotations

import threading
import time
from abc import abstractmethod
from collections import OrderedDict
from src._compat import dataclass
from typing import Any, Optional
from .ports.cache import CachePort


# ═══════════════════════════════════════════════════════════════
# 缓存条目
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class _CacheItem:
    """缓存条目 — 包装值和过期时间"""

    value: Any
    expires_at: float

    @classmethod
    def create(cls, value: Any, ttl: float) -> "_CacheItem":
        return cls(value, time.monotonic() + ttl if ttl > 0 else float("inf"))

    @property
    def expired(self) -> bool:
        return time.monotonic() > self.expires_at


# ═══════════════════════════════════════════════════════════════
# LRUCache — 最近最少使用缓存
# ═══════════════════════════════════════════════════════════════

class LRUCache(CachePort):
    """线程安全的 LRU 缓存

    使用线程锁保护 OrderedDict，所有读写操作（含 get 的 move_to_end）
    在同一锁内完成，确保 LRU 顺序在每次访问时均正确更新。

    使用方式:
        cache = LRUCache(maxsize=1000, default_ttl=300)
        cache.set("key", value)
        val = cache.get("key")
    """

    def __init__(self, maxsize: int = 1000, default_ttl: float = 300):
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._data: OrderedDict[str, _CacheItem] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            if item.expired:
                # 过期条目由 set()/get_or_set() 的 _evict() 延迟清理。
                return None
            # 在读时更新 LRU 顺序，确保最近访问的条目被保护不被淘汰。
            self._data.move_to_end(key)
            return item.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        with self._lock:
            self._data[key] = _CacheItem.create(value, ttl)
            self._data.move_to_end(key)
            self._evict()

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._data:
                del self._data[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def has(self, key: str) -> bool:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return False
            if item.expired:
                # 过期条目由 set()/get_or_set() 的 _evict() 延迟清理。
                return False
            return True

    def get_or_set(self, key: str, factory, ttl: Optional[float] = None) -> Any:
        """获取缓存值，不存在则通过 factory 创建"""
        ttl = ttl if ttl is not None else self._default_ttl
        # 先尝试读缓存（get 已包含 move_to_end）
        value = self.get(key)
        if value is not None:
            return value
        # 不存在，加锁创建（双重检查锁）
        with self._lock:
            item = self._data.get(key)
            if item is not None and not item.expired:
                return item.value
            value = factory()
            self._data[key] = _CacheItem.create(value, ttl)
            self._data.move_to_end(key)
            self._evict()
            return value

    def invalidate_pattern(self, pattern: str) -> int:
        """按前缀批量失效"""
        count = 0
        with self._lock:
            keys_to_delete = [k for k in self._data if k.startswith(pattern)]
            for k in keys_to_delete:
                del self._data[k]
                count += 1
        return count

    def _evict(self) -> None:
        """淘汰超出 maxsize 的条目（淘汰最早访问的）"""
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    @property
    def size(self) -> int:
        """当前缓存条目数"""
        with self._lock:
            return len(self._data)

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def stats(self) -> dict:
        """缓存统计信息"""
        with self._lock:
            return {
                "size": len(self._data),
                "maxsize": self._maxsize,
                "default_ttl": self._default_ttl,
            }


# ═══════════════════════════════════════════════════════════════
# NullCache — 空缓存（不存储任何数据）
# ═══════════════════════════════════════════════════════════════

class NullCache(CachePort):
    """空缓存 — 所有操作无效果，用于测试或关闭缓存场景"""

    def get(self, key: str) -> Optional[Any]:
        return None

    def set(self, key: str, value: Any, ttl: float = 300) -> None:
        pass

    def delete(self, key: str) -> bool:
        return False

    def clear(self) -> None:
        pass

    def has(self, key: str) -> bool:
        return False

    def get_or_set(self, key: str, factory, ttl: float = 300) -> Any:
        return factory()

    def invalidate_pattern(self, pattern: str) -> int:
        return 0


# ═══════════════════════════════════════════════════════════════
# 模块级全局缓存实例
# ═══════════════════════════════════════════════════════════════

_default_cache: CachePort | None = None
_cache_lock = threading.RLock()


def get_default_cache() -> CachePort:
    """获取全局默认缓存（线程安全单例）"""
    global _default_cache
    if _default_cache is None:
        with _cache_lock:
            if _default_cache is None:
                _default_cache = LRUCache()
    return _default_cache


def set_default_cache(cache: CachePort) -> None:
    """设置全局默认缓存（用于测试/依赖注入）"""
    global _default_cache
    with _cache_lock:
        _default_cache = cache


def reset_default_cache() -> None:
    """重置全局默认缓存（主要用于测试）"""
    global _default_cache
    with _cache_lock:
        _default_cache = None
