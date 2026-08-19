"""src/core/adapters/stats — DefaultStatsAdapter / MockStatsAdapter 单元测试。

覆盖：
  - DefaultStatsAdapter：转发到 src/api.stats 全局函数（monkeypatch 验证透传）
  - MockStatsAdapter：纯内存统计（累积/窗口均值/快照/重置）
"""

from __future__ import annotations

import pytest

from src.core.adapters.stats import DefaultStatsAdapter, MockStatsAdapter


# ── MockStatsAdapter：纯内存逻辑 ───────────────────────────

def test_mock_accumulate_usage():
    m = MockStatsAdapter()
    m.accumulate_usage(100, 50)
    m.accumulate_usage(25, 75)
    assert m.get_total_input_tokens() == 125
    assert m.get_total_output_tokens() == 125


def test_mock_speed_and_windows():
    m = MockStatsAdapter()
    assert m.get_token_speed() == 0.0
    assert m.get_avg_token_speed() == 0.0
    assert m.get_short_window_speed() == 0.0

    for s in (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0):
        m.set_stream_speed(s)
    assert m.get_token_speed() == 70.0
    assert m.get_avg_token_speed() == pytest.approx(40.0)
    # 短窗口 = 最近 5 个速度的均值
    assert m.get_short_window_speed() == pytest.approx(50.0)


def test_mock_tool_parse_elapsed():
    m = MockStatsAdapter()
    m.set_tool_parse_elapsed(1.5)
    assert m.tool_parse_elapsed == 1.5


def test_mock_snapshot():
    m = MockStatsAdapter()
    m.accumulate_usage(10, 20)
    m.set_stream_speed(5.0)
    snap = m.snapshot()
    assert snap["input"] == 10
    assert snap["output"] == 20
    assert snap["speed"] == 5.0
    assert snap["avg_speed"] == 5.0


def test_mock_reset():
    m = MockStatsAdapter()
    m.accumulate_usage(10, 20)
    m.set_stream_speed(5.0)
    m.set_tool_parse_elapsed(2.0)
    m.reset()
    assert m.get_total_input_tokens() == 0
    assert m.get_total_output_tokens() == 0
    assert m.get_token_speed() == 0.0
    assert m.tool_parse_elapsed == 0.0
    assert m.get_avg_token_speed() == 0.0
    assert m.snapshot()["speed"] == 0.0


# ── DefaultStatsAdapter：透传到 api.stats ─────────────────

@pytest.fixture
def fake_stats(monkeypatch):
    """用记录式假模块替换 src.api.stats 的全局函数，验证透传。

    DefaultStatsAdapter 在方法体内 ``from ...api.stats import xxx`` 延迟导入，
    因此必须 patch ``src.api.stats`` 上的名字（patch 适配器模块无效）。
    """
    calls = {"accumulate": [], "tool_parse": None, "speed": None, "reset": 0}

    import src.api.stats as api_stats

    class _FakeStats:
        @staticmethod
        def accumulate_usage(usage):
            calls["accumulate"].append(usage)

        @staticmethod
        def set_tool_parse_elapsed(elapsed):
            calls["tool_parse"] = elapsed

        @staticmethod
        def set_stream_speed(speed):
            calls["speed"] = speed

        @staticmethod
        def get_total_input_tokens():
            return 11

        @staticmethod
        def get_total_output_tokens():
            return 22

        @staticmethod
        def get_token_speed():
            return 3.0

        @staticmethod
        def get_avg_token_speed():
            return 4.0

        @staticmethod
        def get_short_window_speed():
            return 5.0

        @staticmethod
        def get_token_stats():
            return {"input": 1, "output": 2, "speed": 3.0}

        @staticmethod
        def reset_stats():
            calls["reset"] += 1

    for name in (
        "accumulate_usage", "set_tool_parse_elapsed", "set_stream_speed",
        "get_total_input_tokens", "get_total_output_tokens", "get_token_speed",
        "get_avg_token_speed", "get_short_window_speed", "get_token_stats",
        "reset_stats",
    ):
        monkeypatch.setattr(api_stats, name, getattr(_FakeStats, name))
    return calls


def test_default_adapter_accumulate(fake_stats):
    d = DefaultStatsAdapter()
    d.accumulate_usage(7, 8)
    assert fake_stats["accumulate"] == [{"input": 7, "output": 8}]


def test_default_adapter_setters(fake_stats):
    d = DefaultStatsAdapter()
    d.set_tool_parse_elapsed(0.25)
    d.set_stream_speed(9.5)
    assert fake_stats["tool_parse"] == 0.25
    assert fake_stats["speed"] == 9.5


def test_default_adapter_getters(fake_stats):
    d = DefaultStatsAdapter()
    assert d.get_total_input_tokens() == 11
    assert d.get_total_output_tokens() == 22
    assert d.get_token_speed() == 3.0
    assert d.get_avg_token_speed() == 4.0
    assert d.get_short_window_speed() == 5.0


def test_default_adapter_snapshot(fake_stats):
    d = DefaultStatsAdapter()
    assert d.snapshot() == {"input": 1, "output": 2, "speed": 3.0}


def test_default_adapter_snapshot_non_dict(fake_stats, monkeypatch):
    """snapshot 遇到非 dict 返回值时兜底为空 dict。"""
    import src.api.stats as api_stats

    monkeypatch.setattr(api_stats, "get_token_stats", lambda: None)
    d = DefaultStatsAdapter()
    assert d.snapshot() == {}


def test_default_adapter_reset(fake_stats):
    d = DefaultStatsAdapter()
    d.reset()
    assert fake_stats["reset"] == 1
