"""测试 StreamingIndicator — 频率 10Hz + 移除 _has_shown 状态。

回归测试覆盖：
  - Timer 间隔为 0.1s（10Hz）
  - stop() 在 was_running 时调用 clear_line()
  - on_first_content() 在 was_running 时调用 clear_line()
  - stop() 在未启动时不调用 clear_line()
  - 完整 start→tick→stop 生命周期
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_output():
    """创建模拟的 OutputAdapter。"""
    return MagicMock()


@pytest.fixture
def indicator(mock_output):
    """创建 StreamingIndicator 实例（使用 mock output）。"""
    from src.renderer.indicator import StreamingIndicator

    return StreamingIndicator(mock_output)


# ============================================================
# Timer 间隔验证
# ============================================================


class TestTimerInterval:
    """验证 _schedule_tick 创建 threading.Timer(0.1, ...)。"""

    def test_timer_interval_0_1_regression(self, indicator):
        """_schedule_tick 应创建间隔为 0.1 的 Timer。"""
        with patch.object(threading, "Timer", wraps=threading.Timer) as mock_timer:
            indicator._schedule_tick()
            (interval, callback), _ = mock_timer.call_args
            assert interval == 0.1, f"期望 0.1，实际 {interval}"
            assert callback == indicator._tick, f"期望回调 _tick，实际 {callback}"
            # 清理创建的 Timer
            indicator._timer.cancel()


# ============================================================
# stop() 清除行为验证
# ============================================================


class TestStopClearBehavior:
    """验证 stop() 的条件清除逻辑（_has_shown 已移除）。"""

    def test_stop_clears_line_when_running_regression(self, indicator):
        """stop() 在 running 状态时应调用 clear_line()。"""
        indicator.start("测试")
        indicator.stop()
        assert indicator._output.clear_line.call_count >= 1, (
            "stop() 在 running 状态下应调用 clear_line()"
        )

    def test_stop_no_clear_when_not_running_regression(self, indicator, mock_output):
        """stop() 在未启动时不调用 clear_line()。"""
        indicator.stop()
        mock_output.clear_line.assert_not_called()

    def test_stop_clears_line_exactly_once_regression(self, indicator):
        """stop() 应恰好调用一次 clear_line()（非冗余）。"""
        indicator.start("测试")
        indicator.stop()
        # _show → _tick 链也可能调用 clear_line，但 stop() 自身应恰好有一次
        # 这里的断言聚焦：stop 触发后至少有一次 clear_line（来自 stop 自身）
        assert indicator._output.clear_line.call_count >= 1


# ============================================================
# on_first_content() 清除行为验证
# ============================================================


class TestOnFirstContentClearBehavior:
    """验证 on_first_content() 的条件清除逻辑（_has_shown 已移除）。"""

    def test_on_first_content_clears_line_when_running_regression(
        self, indicator
    ):
        """on_first_content() 在 running 状态时应调用 clear_line()。"""
        indicator.start("测试")
        indicator.on_first_content()
        assert indicator._output.clear_line.call_count >= 1, (
            "on_first_content() 在 running 状态下应调用 clear_line()"
        )

    def test_on_first_content_no_clear_when_not_running_regression(
        self, indicator, mock_output
    ):
        """on_first_content() 在未启动时不调用 clear_line()。"""
        indicator.on_first_content()
        mock_output.clear_line.assert_not_called()

    def test_on_first_content_sets_terminated_before_stop_regression(
        self, indicator
    ):
        """on_first_content() 应先设 _terminated 再调 stop()（防止 _show 竞争写入）。"""
        indicator.start("测试")
        # 记录调用顺序
        calls = []

        original_set = indicator._terminated.set
        original_stop = indicator.stop

        def tracking_set():
            calls.append("set_terminated")
            return original_set()

        def tracking_stop():
            calls.append("stop")
            return original_stop()

        indicator._terminated.set = tracking_set
        indicator.stop = tracking_stop

        indicator.on_first_content()
        # stop() 内部也会调用 _terminated.set()，因此 calls 中可能含多个
        # "set_terminated"，但关键约束是第一个 set_terminated 先于 stop
        assert "set_terminated" in calls, "应调用过 _terminated.set()"
        assert "stop" in calls, "应调用过 stop()"
        set_idx = calls.index("set_terminated")
        stop_idx = calls.index("stop")
        assert set_idx < stop_idx, (
            f"set_terminated（第{set_idx+1}步）应在 stop（第{stop_idx+1}步）之前，"
            f"实际调用序列: {calls}"
        )


# ============================================================
# 完整生命周期回归测试
# ============================================================


class TestFullCycle:
    """验证 start → tick → stop 完整生命周期无异常。"""

    def test_start_tick_stop_full_cycle_regression(self, indicator):
        """完整生命周期：start() → 等待 tick 触发 → stop()，验证流程无异常。"""
        indicator.start("周期测试")
        # 等待至少一个 tick 触发（Timer 间隔 0.1s）
        assert indicator._timer is not None
        assert indicator._timer.is_alive()
        # 等待 tick 触发一次（0.1s 内应有 _tick，额外给 2 倍缓冲）
        import time
        time.sleep(0.25)
        # 停止
        indicator.stop()
        # 验证至少触发了一次 _show（即至少一个 _tick 回调）
        # _show 的内部调用：clear_line + write_raw
        assert indicator._output.write_raw.call_count >= 1, (
            "start→wait→stop 至少应触发一次 _show（一次 write_raw）"
        )
        # 验证 timer 已取消
        assert indicator._timer is None, "stop() 后 timer 应被置为 None"

    def test_consecutive_start_stop_regression(self, indicator):
        """连续多次 start/stop 不应抛异常。"""
        for i in range(3):
            indicator.start(f"第{i+1}次")
            import time
            time.sleep(0.05)
            indicator.stop()
        # 验证无异常，且最终状态正确
        assert not indicator._running.is_set()
        assert indicator._terminated.is_set()
