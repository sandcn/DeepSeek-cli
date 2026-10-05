"""缓存插件 — 提供 ``ctx.cache``。

「一切皆插件」：通用缓存（``LRUCache`` / 可替换 ``CachePort``）由内核服务
独占，``get_default_cache()`` 内核优先返回该实例；内核缺失（单元测试、
独立调用）时回退进程级单例。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class CacheService(Service):
    """缓存服务 — 占据 ``ctx.cache``。"""

    provide = "cache"
    name = "cache"

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.cache import LRUCache

        self._cache = LRUCache()

    @property
    def cache(self):
        return self._cache

    def get(self, key: str):
        return self._cache.get(key)

    def set(self, key: str, value, ttl=None) -> None:
        self._cache.set(key, value, ttl)

    def delete(self, key: str) -> bool:
        return self._cache.delete(key)

    def clear(self) -> None:
        self._cache.clear()


@plugin("cache", provide=["cache"])
def apply(ctx):
    return CacheService(ctx)


__all__ = ["CacheService", "apply"]
