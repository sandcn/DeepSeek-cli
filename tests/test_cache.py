"""Tests for src/core/cache.py — LRUCache, NullCache, 和全局缓存管理函数"""

import time

import pytest

from src.core.cache import (
    LRUCache,
    NullCache,
    CachePort,
    get_default_cache,
    reset_default_cache,
    set_default_cache,
)


# ═══════════════════════════════════════════════════════════════
# CachePort 端口接口导入测试
# ═══════════════════════════════════════════════════════════════

class TestCachePortFromPorts:
    """验证 CachePort 可从 ports 包正确导入（步骤 3 迁移验证）"""

    def test_cache_port_from_ports_cache(self):
        from src.core.ports.cache import CachePort as PortCachePort
        assert PortCachePort is CachePort

    def test_cache_port_from_ports_init(self):
        from src.core.ports import CachePort as PortCachePort
        assert PortCachePort is CachePort


# ═══════════════════════════════════════════════════════════════
# LRUCache 测试
# ═══════════════════════════════════════════════════════════════

class TestLRUCache:
    """LRUCache 功能测试"""

    # ── 基本 set / get ──────────────────────────────────────────

    def test_set_and_get(self):
        cache = LRUCache(maxsize=10, default_ttl=300)
        cache.set("name", "alice")
        assert cache.get("name") == "alice"

    def test_get_nonexistent_returns_none(self):
        cache = LRUCache()
        assert cache.get("nonexistent") is None

    def test_get_after_delete_returns_none(self):
        cache = LRUCache()
        cache.set("key", "val")
        cache.delete("key")
        assert cache.get("key") is None

    # ── 过期 ────────────────────────────────────────────────────

    def test_expired_returns_none(self):
        """使用极小的 ttl 测试过期后 get 返回 None"""
        cache = LRUCache(maxsize=10, default_ttl=300)
        cache.set("key", "value", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("key") is None

    # ── LRU 淘汰 ────────────────────────────────────────────────

    def test_lru_eviction(self):
        """maxsize=2 设 3 个值，最早的值被淘汰"""
        cache = LRUCache(maxsize=2, default_ttl=300)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        # a 是最早的，应被淘汰
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    def test_lru_eviction_set_updates_order(self):
        """新 set 的键排在最后，淘汰最早 set 的键"""
        cache = LRUCache(maxsize=2, default_ttl=300)
        cache.set("a", 1)
        cache.set("b", 2)
        # set("c") 淘汰最旧的 "a"
        cache.set("c", 3)
        assert cache.get("a") is None
        assert cache.get("b") == 2
        assert cache.get("c") == 3

    # ── has() ──────────────────────────────────────────────────

    def test_has_exists(self):
        cache = LRUCache()
        cache.set("key", "value")
        assert cache.has("key") is True

    def test_has_not_exists(self):
        cache = LRUCache()
        assert cache.has("nonexistent") is False

    def test_has_expired_returns_false(self):
        cache = LRUCache()
        cache.set("key", "value", ttl=0.01)
        time.sleep(0.02)
        assert cache.has("key") is False

    # ── delete() ───────────────────────────────────────────────

    def test_delete_exists_returns_true(self):
        cache = LRUCache()
        cache.set("key", "value")
        assert cache.delete("key") is True

    def test_delete_not_exists_returns_false(self):
        cache = LRUCache()
        assert cache.delete("nonexistent") is False

    # ── clear() ────────────────────────────────────────────────

    def test_clear_empties_cache(self):
        cache = LRUCache()
        cache.set("a", 1)
        cache.set("b", 2)
        cache.clear()
        assert cache.get("a") is None
        assert cache.get("b") is None
        assert cache.size == 0

    # ── get_or_set() ───────────────────────────────────────────

    def test_get_or_set_first_call_invokes_factory(self):
        cache = LRUCache()
        factory_called = []

        def factory():
            factory_called.append(True)
            return "computed"

        result = cache.get_or_set("key", factory)
        assert result == "computed"
        assert len(factory_called) == 1

    def test_get_or_set_second_call_uses_cache(self):
        cache = LRUCache()
        cache.set("key", "cached_value")
        factory_called = []

        def factory():
            factory_called.append(True)
            return "should_not_be_used"

        result = cache.get_or_set("key", factory)
        assert result == "cached_value"
        assert len(factory_called) == 0

    # ── invalidate_pattern() ────────────────────────────────────

    def test_invalidate_pattern_by_prefix(self):
        cache = LRUCache()
        cache.set("prefix_a", 1)
        cache.set("prefix_b", 2)
        cache.set("other", 3)
        count = cache.invalidate_pattern("prefix_")
        assert count == 2
        assert cache.get("prefix_a") is None
        assert cache.get("prefix_b") is None
        assert cache.get("other") == 3

    def test_invalidate_pattern_no_match(self):
        cache = LRUCache()
        cache.set("a", 1)
        cache.set("b", 2)
        count = cache.invalidate_pattern("nonexistent_")
        assert count == 0
        assert cache.get("a") == 1
        assert cache.get("b") == 2

    # ── 属性：size / maxsize / stats ────────────────────────────

    def test_size_property(self):
        cache = LRUCache()
        assert cache.size == 0
        cache.set("a", 1)
        assert cache.size == 1
        cache.set("b", 2)
        assert cache.size == 2
        cache.delete("a")
        assert cache.size == 1

    def test_maxsize_property(self):
        cache = LRUCache(maxsize=500)
        assert cache.maxsize == 500

    def test_maxsize_default(self):
        cache = LRUCache()
        assert cache.maxsize == 1000

    def test_stats(self):
        cache = LRUCache(maxsize=200, default_ttl=600)
        cache.set("a", 1)
        cache.set("b", 2)
        stats = cache.stats()
        assert stats["size"] == 2
        assert stats["maxsize"] == 200
        assert stats["default_ttl"] == 600

    # ── 边界条件 ────────────────────────────────────────────────

    def test_set_overwrites_existing_key(self):
        cache = LRUCache()
        cache.set("key", "old")
        cache.set("key", "new")
        assert cache.get("key") == "new"
        assert cache.size == 1

    def test_custom_ttl_overrides_default(self):
        """set 时传入 ttl 应覆盖 default_ttl"""
        cache = LRUCache(maxsize=10, default_ttl=300)
        cache.set("short", "value", ttl=0.01)
        cache.set("long", "value", ttl=100)
        time.sleep(0.02)
        assert cache.get("short") is None
        assert cache.get("long") == "value"

    def test_get_or_set_with_custom_ttl(self):
        cache = LRUCache(default_ttl=300)
        result = cache.get_or_set("key", lambda: "val", ttl=0.01)
        assert result == "val"
        time.sleep(0.02)
        assert cache.get("key") is None


# ═══════════════════════════════════════════════════════════════
# NullCache 测试
# ═══════════════════════════════════════════════════════════════

class TestNullCache:
    """NullCache — 所有操作无效果"""

    def test_get_always_returns_none(self):
        cache = NullCache()
        cache.set("key", "value")
        assert cache.get("key") is None

    def test_get_nonexistent_returns_none(self):
        cache = NullCache()
        assert cache.get("any") is None

    def test_set_does_nothing(self):
        cache = NullCache()
        cache.set("key", "value")  # 不应抛出异常
        assert cache.get("key") is None

    def test_delete_returns_false(self):
        cache = NullCache()
        assert cache.delete("key") is False

    def test_clear_does_not_raise(self):
        cache = NullCache()
        cache.clear()  # 不应抛出异常

    def test_has_returns_false(self):
        cache = NullCache()
        cache.set("key", "value")
        assert cache.has("key") is False

    def test_get_or_set_invokes_factory_every_time(self):
        """NullCache 的 get_or_set 每次都会调用 factory"""
        cache = NullCache()
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return f"val_{call_count}"

        r1 = cache.get_or_set("key", factory)
        r2 = cache.get_or_set("key", factory)
        assert r1 == "val_1"
        assert r2 == "val_2"
        assert call_count == 2

    def test_invalidate_pattern_returns_zero(self):
        cache = NullCache()
        assert cache.invalidate_pattern("any") == 0


# ═══════════════════════════════════════════════════════════════
# 全局缓存管理测试
# ═══════════════════════════════════════════════════════════════

class TestGlobalCache:
    """get_default_cache / set_default_cache / reset_default_cache"""

    @pytest.fixture(autouse=True)
    def _reset_before_each(self):
        """每个测试前重置全局缓存状态"""
        reset_default_cache()
        yield
        reset_default_cache()

    def test_get_default_cache_returns_lru_cache_instance(self):
        cache = get_default_cache()
        assert isinstance(cache, LRUCache)

    def test_get_default_cache_is_singleton(self):
        c1 = get_default_cache()
        c2 = get_default_cache()
        assert c1 is c2

    def test_set_default_cache(self):
        custom = NullCache()
        set_default_cache(custom)
        assert get_default_cache() is custom

    def test_reset_default_cache(self):
        set_default_cache(NullCache())
        assert isinstance(get_default_cache(), NullCache)
        reset_default_cache()
        assert isinstance(get_default_cache(), LRUCache)

    def test_reset_then_get_is_new_instance(self):
        c1 = get_default_cache()
        reset_default_cache()
        c2 = get_default_cache()
        assert c1 is not c2
