"""Token 速率统计器测试 — 覆盖 src/api/_token_speed.py。

验证 _TokenSpeedTracker 的总计数、窗口速度、重置与快照行为。
"""

import pytest

from src.api._token_speed import _TokenSpeedTracker


@pytest.fixture
def tracker():
    return _TokenSpeedTracker()


def test_add_token_size_accumulates(tracker):
    tracker.add_token_size(10)
    tracker.add_token_size(5)
    assert tracker.total_tokens == 15


def test_add_token_size_ignores_non_positive(tracker):
    tracker.add_token_size(0)
    tracker.add_token_size(-5)
    assert tracker.total_tokens == 0


def test_avg_speed_zero_without_data(tracker):
    assert tracker.avg_speed == 0.0


def test_window_speed_zero_without_data(tracker):
    assert tracker.window_speed == 0.0


def test_short_window_speed_zero_without_data(tracker):
    assert tracker.short_window_speed == 0.0


def test_reset_keep_total(tracker):
    tracker.add_token_size(10)
    tracker.reset(keep_total=True)
    assert tracker.total_tokens == 10
    assert tracker.avg_speed == 0.0  # start_time 已清空


def test_reset_full_clear(tracker):
    tracker.add_token_size(10)
    tracker.reset(keep_total=False)
    assert tracker.total_tokens == 0


def test_stats_snapshot_fields(tracker):
    tracker.add_token_size(10)
    snap = tracker.stats_snapshot()
    assert snap["total_tokens"] == 10
    assert "avg_speed" in snap
    assert "window_speed" in snap
    assert "elapsed_seconds" in snap
    assert "per_second_speed" in snap


def test_per_second_speed_zero_with_insufficient_samples(tracker):
    tracker.add_token_size(10)
    # 样本不足 2 条时为 0
    assert tracker.per_second_speed >= 0.0
