"""会话统计核心测试 — 覆盖 src/api/_stats_core.py。

验证 _Stats 的 usage 累加、调用计数、工具解析耗时与快照副本。
"""

import pytest

from src.api._stats_core import _Stats


@pytest.fixture
def stats():
    return _Stats()


def test_accumulate_usage(stats):
    stats.accumulate_usage({"input": 10, "output": 5, "input_cache_hit": 3, "input_cache_miss": 7})
    snap = stats.get_stats_snapshot()
    assert snap["input"] == 10
    assert snap["output"] == 5
    assert snap["input_cache_hit"] == 3
    assert snap["input_cache_miss"] == 7


def test_accumulate_usage_increment_calls(stats):
    stats.accumulate_usage({"input": 1, "output": 1})
    stats.accumulate_usage({"input": 1, "output": 1})
    assert stats.get_stats_snapshot()["calls"] == 2


def test_accumulate_usage_no_increment_calls(stats):
    stats.accumulate_usage({"input": 1, "output": 1}, increment_calls=False)
    assert stats.get_stats_snapshot()["calls"] == 0


def test_accumulate_usage_missing_cache_fields(stats):
    # 缓存字段缺省按 0 累加
    stats.accumulate_usage({"input": 5, "output": 2})
    snap = stats.get_stats_snapshot()
    assert snap["input_cache_hit"] == 0
    assert snap["input_cache_miss"] == 0


def test_set_get_tool_parse_elapsed(stats):
    stats.set_tool_parse_elapsed(1.5)
    assert stats.get_last_tool_parse_elapsed() == 1.5


def test_set_get_stream_speed(stats):
    stats.set_stream_speed(42.0)
    assert stats.get_last_stream_speed() == 42.0


def test_get_stats_snapshot_returns_copy(stats):
    stats.accumulate_usage({"input": 1, "output": 1})
    snap = stats.get_stats_snapshot()
    snap["input"] = 999
    assert stats.get_stats_snapshot()["input"] == 1
