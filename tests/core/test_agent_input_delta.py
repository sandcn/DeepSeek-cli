"""Agent 新增输入（上下文增量）累计 — 统计口径测试。"""

from __future__ import annotations

from src.core.stats._stats_core import _Stats


def _usage(input_tokens: int, output: int = 0, hit: int = 0, miss: int = 0) -> dict:
    return {
        "input": input_tokens,
        "output": output,
        "input_cache_hit": hit,
        "input_cache_miss": miss,
    }


def test_first_call_counts_full_and_growth_counts_delta():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label="assistant", model="m1")
    assert s.get_stats_snapshot()["input_delta"] == 1000
    assert s.get_stats_snapshot()["input"] == 1000

    s.accumulate_usage(_usage(1200), label="assistant", model="m1")
    snap = s.get_stats_snapshot()
    assert snap["input_delta"] == 1200  # 1000 + (1200 - 1000)
    assert snap["input"] == 2200  # 计费口径仍为 full prompt 累加


def test_main_labels_share_one_baseline():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label=None, model="m1")
    s.accumulate_usage(_usage(1200), label="assistant", model="m1")
    s.accumulate_usage(_usage(1500), label="main", model="m1")
    assert s.get_stats_snapshot()["input_delta"] == 1500


def test_subagents_have_isolated_baselines():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label="assistant", model="m1")
    s.accumulate_usage(_usage(500), label="agent-1", model="m1")
    s.accumulate_usage(_usage(600), label="agent-1", model="m1")
    s.accumulate_usage(_usage(300), label="sa-bg1", model="m1")
    snap = s.get_stats_snapshot()
    # 1000 + 500 + 100 + 300
    assert snap["input_delta"] == 1900
    assert snap["input"] == 2400


def test_internal_calls_are_excluded_from_agent_input():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label="assistant", model="m1")
    before = s.get_stats_snapshot()["input_delta"]
    for label in ("summarize", "session-title", "loop-prompt"):
        s.accumulate_usage(_usage(9999), label=label, model="m1")
    snap = s.get_stats_snapshot()
    assert snap["input_delta"] == before
    assert snap["input"] == 1000 + 3 * 9999  # 计费/缓存明细仍包含内部调用


def test_shrink_resets_baseline_and_counts_full():
    s = _Stats()
    s.accumulate_usage(_usage(1200), label="assistant", model="m1")
    s.accumulate_usage(_usage(800), label="assistant", model="m1")  # 压缩/重建
    assert s.get_stats_snapshot()["input_delta"] == 2000  # 1200 + 800
    s.accumulate_usage(_usage(900), label="assistant", model="m1")
    assert s.get_stats_snapshot()["input_delta"] == 2100  # +100


def test_model_switch_starts_new_baseline():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label="assistant", model="m1")
    s.accumulate_usage(_usage(1000), label="assistant", model="m2")
    assert s.get_stats_snapshot()["input_delta"] == 2000


def test_estimate_updates_do_not_touch_delta():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label="assistant", model="m1")
    s.accumulate_usage(
        {"input": 0, "output": 50}, increment_calls=False,
        label="assistant", model="m1",
    )
    assert s.get_stats_snapshot()["input_delta"] == 1000


def test_baseline_reset_keeps_accumulated_delta():
    s = _Stats()
    s.accumulate_usage(_usage(1000), label="assistant", model="m1")
    s.reset_agent_input_baseline()
    assert s.get_stats_snapshot()["input_delta"] == 1000
    s.accumulate_usage(_usage(700), label="assistant", model="m1")
    assert s.get_stats_snapshot()["input_delta"] == 1700  # 新基线计全量
