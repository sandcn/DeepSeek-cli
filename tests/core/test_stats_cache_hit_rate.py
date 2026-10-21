"""缓存命中率（dsh 口径）— 纯函数测试。"""

from __future__ import annotations

import pytest

from src.core.stats import compute_cache_hit_rate


def test_hit_rate_is_hit_over_hit_plus_miss():
    """dsh 口径：命中 / (命中 + 未命中)，与输入总量字段无关。"""
    assert compute_cache_hit_rate({
        "input": 1000,
        "output": 200,
        "input_cache_hit": 875,
        "input_cache_miss": 125,
    }) == pytest.approx(0.875)


def test_hit_rate_ignores_output_tokens():
    """输出 token 不进分母。"""
    assert compute_cache_hit_rate({
        "input": 100,
        "output": 9000,
        "input_cache_hit": 50,
        "input_cache_miss": 50,
    }) == pytest.approx(0.5)


def test_zero_hits_with_misses_is_real_zero_percent():
    """真实零命中返回 0.0（区别于「无缓存数据」）。"""
    assert compute_cache_hit_rate({
        "input": 100,
        "input_cache_hit": 0,
        "input_cache_miss": 100,
    }) == 0.0


@pytest.mark.parametrize("stats", [
    None,
    {},
    {"input": 1000},
    {"input": 1000, "input_cache_hit": 0, "input_cache_miss": 0},
])
def test_no_cache_split_returns_none(stats):
    """无缓存拆分数据（provider 未上报 / 尚未收到真实 usage）→ 不可用。"""
    assert compute_cache_hit_rate(stats) is None


def test_negative_values_are_clamped_and_invalid_is_unavailable():
    assert compute_cache_hit_rate({
        "input_cache_hit": -5,
        "input_cache_miss": 10,
    }) == 0.0
    assert compute_cache_hit_rate({
        "input_cache_hit": "not-a-number",
        "input_cache_miss": 10,
    }) is None
