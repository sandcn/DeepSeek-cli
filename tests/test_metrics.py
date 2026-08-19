"""结构化指标收集器测试 — 覆盖 src/core/telemetry/metrics.py。

验证 Counter / Gauge / Histogram 三类指标与快照。
"""

import pytest

from src.core.telemetry.metrics import MetricsCollector


@pytest.fixture
def mc():
    return MetricsCollector()


def test_counter_increment(mc):
    mc.counter("api.calls")
    mc.counter("api.calls", 2)
    assert mc.get_counter("api.calls") == 3


def test_counter_default_zero(mc):
    assert mc.get_counter("missing") == 0


def test_gauge_set_get(mc):
    mc.gauge("context.chars", 15420)
    assert mc.get_gauge("context.chars") == 15420


def test_gauge_missing_none(mc):
    assert mc.get_gauge("missing") is None


def test_gauge_add(mc):
    mc.gauge_add("g", 5)
    mc.gauge_add("g", -2)
    assert mc.get_gauge("g") == 3.0


def test_histogram_stats(mc):
    mc.histogram("latency", 1.0)
    mc.histogram("latency", 2.0)
    mc.histogram("latency", 3.0)
    stats = mc.get_histogram_stats("latency")
    assert stats["count"] == 3
    assert stats["min"] == 1.0
    assert stats["max"] == 3.0
    assert stats["avg"] == 2.0
    assert "p50" in stats
    assert "p99" in stats


def test_histogram_stats_empty(mc):
    assert mc.get_histogram_stats("missing") is None


def test_reset_histogram(mc):
    mc.histogram("latency", 1.0)
    mc.reset_histogram("latency")
    assert mc.get_histogram_stats("latency") is None


def test_snapshot(mc):
    mc.counter("c", 1)
    mc.gauge("g", 2.0)
    mc.histogram("h", 1.0)
    snap = mc.snapshot()
    assert snap["counters"]["c"] == 1
    assert snap["gauges"]["g"] == 2.0
    assert "h" in snap["histograms"]
    assert "uptime_seconds" in snap


def test_snapshot_reset_histograms(mc):
    mc.histogram("h", 1.0)
    mc.snapshot(reset_histograms=True)
    assert mc.get_histogram_stats("h") is None


def test_to_jsonl(mc):
    mc.counter("c", 1)
    s = mc.to_jsonl()
    assert "metrics_snapshot" in s


def test_report(mc):
    mc.counter("c", 1)
    text = mc.report()
    assert "指标快照" in text
