"""Tests for tui_framework.core.ttl_cache module."""
import time
import pytest
from tui_framework.core.ttl_cache import TTLCache


class TestTTLCache:
    """Tests for TTLCache."""

    def test_basic_get(self):
        """Cache should return fetcher result."""
        cache = TTLCache(fetcher=lambda: "hello", ttl=60.0)
        assert cache.get() == "hello"

    def test_cache_returns_cached_value(self):
        """Second get within TTL should return cached value."""
        call_count = [0]

        def fetcher():
            call_count[0] += 1
            return call_count[0]

        cache = TTLCache(fetcher=fetcher, ttl=10.0)
        assert cache.get() == 1
        assert cache.get() == 1  # cached
        assert call_count[0] == 1

    def test_refresh_bypasses_ttl(self):
        """refresh() should always call fetcher."""
        call_count = [0]

        def fetcher():
            call_count[0] += 1
            return call_count[0]

        cache = TTLCache(fetcher=fetcher, ttl=10.0)
        assert cache.get() == 1
        assert cache.refresh() == 2
        assert call_count[0] == 2

    def test_clear_resets_cache(self):
        """clear() should cause next get to re-fetch."""
        call_count = [0]

        def fetcher():
            call_count[0] += 1
            return call_count[0]

        cache = TTLCache(fetcher=fetcher, ttl=10.0)
        assert cache.get() == 1
        cache.clear()
        assert cache.get() == 2

    def test_expired_cache_re_fetches(self):
        """Expired cache should re-call fetcher."""
        call_count = [0]

        def fetcher():
            call_count[0] += 1
            return call_count[0]

        cache = TTLCache(fetcher=fetcher, ttl=0.01)
        assert cache.get() == 1
        time.sleep(0.02)
        assert cache.get() == 2

    def test_generic_type(self):
        """Should work with different types."""
        cache = TTLCache(fetcher=lambda: [1, 2, 3], ttl=60.0)
        result = cache.get()
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_zero_ttl_always_re_fetches(self):
        """Zero TTL should re-fetch every time."""
        call_count = [0]

        def fetcher():
            call_count[0] += 1
            return call_count[0]

        cache = TTLCache(fetcher=fetcher, ttl=0.0)
        result = cache.get()
        assert result >= 1  # fetcher was called
        first_count = call_count[0]
        result2 = cache.get()
        assert call_count[0] > first_count  # re-fetched on second call
