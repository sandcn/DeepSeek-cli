"""结构化遥测测试 — 覆盖 src/api/telemetry.py。

验证费用估算的默认单价与模型匹配逻辑。
"""

import pytest

from src.api.telemetry import _estimate_cost, _get_log_path


# ── _estimate_cost ────────────────────────────────────────

def test_estimate_cost_default_input_price():
    # 默认输入单价 0.55 USD/1M tokens
    cost = _estimate_cost("no_such_model_xyz", 1_000_000, 0)
    assert cost == pytest.approx(0.55)


def test_estimate_cost_default_output_price():
    # 默认输出单价 2.19 USD/1M tokens
    cost = _estimate_cost("no_such_model_xyz", 0, 1_000_000)
    assert cost == pytest.approx(2.19)


def test_estimate_cost_zero_tokens():
    assert _estimate_cost("no_such_model_xyz", 0, 0) == 0.0


def test_estimate_cost_combined():
    cost = _estimate_cost("no_such_model_xyz", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.55 + 2.19)


def test_estimate_cost_returns_float():
    assert isinstance(_estimate_cost("m", 100, 200), float)


# ── _get_log_path ─────────────────────────────────────────

def test_get_log_path_ends_with_jsonl():
    path = _get_log_path()
    assert str(path).endswith(".jsonl")


def test_get_log_path_is_cached():
    p1 = _get_log_path()
    p2 = _get_log_path()
    assert p1 == p2
