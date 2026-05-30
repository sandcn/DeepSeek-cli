"""Tests for src/api/stats.py.

测试覆盖：
  - _TokenSpeedTracker 初始化、累加、边界条件、快照、重置
  - _Stats 模块级接口：accumulate_usage / set_tool_parse_elapsed / set_stream_speed / reset_token_speed
"""

import pytest

from src.api.stats import (
    _TokenSpeedTracker,
    add_token_size,
    get_total_tokens,
    get_token_speed,
    get_short_window_speed,
    get_avg_token_speed,
    get_token_speed_snapshot,
    reset_token_speed,
    accumulate_usage,
    get_last_tool_parse_elapsed,
    get_token_stats,
    get_session_start_time,
    set_stream_speed,
    set_tool_parse_elapsed,
    get_last_stream_speed,
)


# ============================================================
# _TokenSpeedTracker
# ============================================================


class TestTokenSpeedTracker:
    """_TokenSpeedTracker 单元测试（独立实例，不影响全局单例）。"""

    def test_init_values(self):
        """初始 total_tokens=0, avg_speed=0.0, window_speed=0.0"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        assert tracker.total_tokens == 0
        assert tracker.avg_speed == 0.0
        assert tracker.window_speed == 0.0
        assert tracker.short_window_speed == 0.0

    def test_add_token_size_accumulates(self):
        """添加 token 后 total_tokens 累加正确"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(10)
        assert tracker.total_tokens == 10

        tracker.add_token_size(20)
        assert tracker.total_tokens == 30

        tracker.add_token_size(5)
        assert tracker.total_tokens == 35

    def test_add_non_positive_size_no_change(self):
        """添加 size<=0 时 total_tokens 无变化"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        assert tracker.total_tokens == 100

        tracker.add_token_size(0)
        assert tracker.total_tokens == 100  # 无变化

        tracker.add_token_size(-10)
        assert tracker.total_tokens == 100  # 无变化

    def test_window_speed_positive_after_add(self):
        """添加 token 后 window_speed > 0（窗口内有数据）"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        assert tracker.window_speed > 0

    def test_short_window_speed_positive_after_add(self):
        """添加 token 后 short_window_speed > 0（1 秒窗口内有数据）"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        assert tracker.short_window_speed > 0

    def test_avg_speed_positive_after_add(self):
        """添加 token 后 avg_speed > 0"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        assert tracker.avg_speed > 0

    def test_stats_snapshot_structure(self):
        """stats_snapshot() 返回正确的 dict 结构"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        snapshot = tracker.stats_snapshot()

        assert isinstance(snapshot, dict)
        assert set(snapshot.keys()) == {
            "total_tokens",
            "avg_speed",
            "window_speed",
            "elapsed_seconds",
            "per_second_speed",
        }
        assert snapshot["total_tokens"] == 0
        assert snapshot["avg_speed"] == 0.0
        assert snapshot["window_speed"] == 0.0
        assert snapshot["elapsed_seconds"] == 0.0
        assert snapshot["per_second_speed"] == 0.0

    def test_stats_snapshot_after_add(self):
        """添加 token 后 stats_snapshot 内容正确"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)

        snapshot = tracker.stats_snapshot()
        assert snapshot["total_tokens"] == 100
        assert snapshot["avg_speed"] > 0
        assert snapshot["window_speed"] > 0
        assert snapshot["elapsed_seconds"] >= 0

    def test_reset_keeps_total_tokens(self):
        """reset() 保留历史累计总 tok，仅清空窗口和计时"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        tracker.reset()

        # 总 tok 是历史累计值，不清空
        assert tracker.total_tokens == 100
        # 窗口速度和平均速度清空（无窗口数据、无计时）
        assert tracker.avg_speed == 0.0
        assert tracker.window_speed == 0.0
        assert tracker.short_window_speed == 0.0

    def test_reset_then_add_works(self):
        """reset() 后再添加，历史累计保留 + 新添加正常累加"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        tracker.reset()

        tracker.add_token_size(50)
        # 历史累计 100 + 新加 50 = 150
        assert tracker.total_tokens == 150
        assert tracker.window_speed > 0

    def test_stats_snapshot_after_reset(self):
        """reset() 后 total_tokens 保留，avg_speed/window_speed 归零"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(100)
        tracker.reset()

        snapshot = tracker.stats_snapshot()
        # total_tokens 是历史累计值，不清空
        assert snapshot["total_tokens"] == 100
        # 窗口清空后 window_speed = 0，无计时 avg_speed = 0
        assert snapshot["avg_speed"] == 0.0
        assert snapshot["window_speed"] == 0.0
        assert snapshot["elapsed_seconds"] == 0.0

    def test_multiple_add_then_window_speed(self):
        """多次添加后 window_speed 应反映所有窗口内 token"""
        tracker = _TokenSpeedTracker(window_seconds=5.0)
        tracker.add_token_size(50)
        tracker.add_token_size(50)
        tracker.add_token_size(50)
        assert tracker.total_tokens == 150
        # 窗口内有 3 条记录，速度应为正数
        assert tracker.window_speed > 0

    def test_window_speed_drops_to_zero_after_window_expires(self):
        """窗口过期后 window_speed 归零（使用短窗口减少等待）"""
        tracker = _TokenSpeedTracker(window_seconds=0.01)  # 10ms 窗口
        tracker.add_token_size(100)
        assert tracker.window_speed > 0

        import time
        time.sleep(0.02)  # 等窗口过期
        assert tracker.window_speed == 0.0


# ============================================================
# 模块级接口（_Stats 单例）
# ============================================================


class TestModuleLevelInterfaces:
    """测试 _Stats 的模块级函数接口。"""

    def setup_method(self):
        """每个测试前完全重置 token 统计（包括总 tok），避免交叉影响"""
        reset_token_speed(keep_total=False)

    def test_accumulate_usage(self):
        """accumulate_usage 累加 input/output/calls"""
        before = get_token_stats()
        accumulate_usage({"input": 10, "output": 20})
        after = get_token_stats()

        assert after["input"] == before["input"] + 10
        assert after["output"] == before["output"] + 20
        assert after["calls"] == before["calls"] + 1

    def test_accumulate_usage_multiple_calls(self):
        """多次 accumulate_usage 累加正确"""
        before = get_token_stats()

        accumulate_usage({"input": 10, "output": 20})
        accumulate_usage({"input": 5, "output": 15})
        after = get_token_stats()

        assert after["input"] == before["input"] + 15
        assert after["output"] == before["output"] + 35
        assert after["calls"] == before["calls"] + 2

    def test_accumulate_usage_partial_fields(self):
        """accumulate_usage 处理缺失字段（只传部分 key）"""
        before = get_token_stats()

        accumulate_usage({"input": 100})
        after = get_token_stats()

        assert after["input"] == before["input"] + 100
        assert after["output"] == before["output"]  # 不变
        assert after["calls"] == before["calls"] + 1

    def test_set_tool_parse_elapsed(self):
        """set_tool_parse_elapsed / get_last_tool_parse_elapsed"""
        assert get_last_tool_parse_elapsed() == 0.0

        set_tool_parse_elapsed(1.23)
        assert get_last_tool_parse_elapsed() == 1.23

        set_tool_parse_elapsed(0.45)
        assert get_last_tool_parse_elapsed() == 0.45

    def test_set_stream_speed(self):
        """set_stream_speed / get_last_stream_speed"""
        assert get_last_stream_speed() == 0.0

        set_stream_speed(12.34)
        assert get_last_stream_speed() == 12.34

        set_stream_speed(56.78)
        assert get_last_stream_speed() == 56.78

    def test_module_add_token_size(self):
        """模块级 add_token_size / get_total_tokens"""
        assert get_total_tokens() == 0

        add_token_size(50)
        assert get_total_tokens() == 50

        add_token_size(30)
        assert get_total_tokens() == 80

    def test_module_token_speeds_positive(self):
        """模块级速度函数在添加 token 后均 > 0"""
        add_token_size(200)

        assert get_token_speed() > 0
        assert get_short_window_speed() > 0
        assert get_avg_token_speed() > 0

    def test_get_token_speed_snapshot(self):
        """get_token_speed_snapshot 返回正确结构"""
        snapshot = get_token_speed_snapshot()

        assert isinstance(snapshot, dict)
        assert set(snapshot.keys()) == {
            "total_tokens",
            "avg_speed",
            "window_speed",
            "elapsed_seconds",
            "per_second_speed",
        }

    def test_get_token_speed_snapshot_after_add(self):
        """添加 token 后快照内容正确"""
        add_token_size(300)
        snapshot = get_token_speed_snapshot()

        assert snapshot["total_tokens"] == 300
        assert snapshot["avg_speed"] > 0
        assert snapshot["window_speed"] > 0
        assert snapshot["elapsed_seconds"] >= 0

    def test_reset_token_speed(self):
        """reset_token_speed 保留历史总 tok，重置窗口和计时"""
        add_token_size(100)
        assert get_total_tokens() > 0

        reset_token_speed()

        # 总 tok 是历史累计值，永不清空
        assert get_total_tokens() == 100
        # 窗口和计时被清空
        assert get_avg_token_speed() == 0.0
        assert get_token_speed() == 0.0
        assert get_short_window_speed() == 0.0

    def test_get_session_start_time(self):
        """get_session_start_time 返回正的时间戳"""
        start = get_session_start_time()
        assert isinstance(start, float)
        assert start > 0
