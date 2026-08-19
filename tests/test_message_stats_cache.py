"""增量消息统计缓存测试 — 覆盖 src/core/internal/shared/_message_stats_cache.py。

验证 MessageStatsCache 的全量同步与增量增删改操作。
"""

import pytest

from src.core.internal.shared._message_stats_cache import MessageStatsCache
from src.core.context_selector import message_to_text
from src.api.tokens import estimate_tokens


def _stats(messages):
    return sum(len(message_to_text(m)) for m in messages)


@pytest.fixture
def cache():
    return MessageStatsCache()


def test_resync(cache):
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "world"},
    ]
    cache.resync(messages)
    assert cache.total_chars == _stats(messages)
    assert cache.is_valid is True
    assert len(cache) == 2


def test_on_append(cache):
    cache.resync([{"role": "user", "content": "a"}])
    before = cache.total_chars
    cache.on_append({"role": "user", "content": "hello"})
    assert cache.total_chars == before + len("hello")
    assert len(cache) == 2


def test_on_insert(cache):
    cache.resync([{"role": "user", "content": "a"}, {"role": "user", "content": "c"}])
    before = cache.total_chars
    cache.on_insert(1, {"role": "user", "content": "bb"})
    assert cache.total_chars == before + 2
    assert len(cache) == 3


def test_on_remove(cache):
    messages = [
        {"role": "user", "content": "aa"},
        {"role": "user", "content": "bb"},
        {"role": "user", "content": "cc"},
    ]
    cache.resync(messages)
    before = cache.total_chars
    cache.on_remove([1])
    assert cache.total_chars == before - 2
    assert len(cache) == 2


def test_on_remove_multiple(cache):
    messages = [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "bb"},
        {"role": "user", "content": "cc"},
    ]
    cache.resync(messages)
    before = cache.total_chars
    cache.on_remove([0, 2])
    assert cache.total_chars == before - 1 - 2
    assert len(cache) == 1


def test_on_replace(cache):
    cache.resync([{"role": "user", "content": "aa"}])
    before = cache.total_chars
    cache.on_replace(0, {"role": "user", "content": "bbbb"})
    assert cache.total_chars == before - 2 + 4
    assert len(cache) == 1


def test_invalidate(cache):
    cache.resync([{"role": "user", "content": "a"}])
    cache.invalidate()
    assert cache.is_valid is False


def test_get_per_msg_out_of_range(cache):
    assert cache.get_per_msg(99) == (0, 0)
