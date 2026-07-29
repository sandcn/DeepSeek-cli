"""Tests for SubAgentPanelController frame rendering debounce."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.tui._subagent_panel import SubAgentPanelController


class TestSubAgentPanelEmitFrameThrottle:
    """Test _emit_frame() time-based throttling."""

    @pytest.fixture
    def controller(self):
        """Create a SubAgentPanelController with mocked _push_frame."""
        ctrl = SubAgentPanelController()
        # Mock _push_frame to avoid DisplayEventBus dependency
        ctrl._push_frame = MagicMock()
        # Mock _render_frame to return empty lines
        ctrl._render_frame = MagicMock(return_value=["line1", "line2"])
        return ctrl

    # ── 场景 1：首次调用 → 正常渲染（时间 >= 0.1 时） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_first_emit_renders_when_time_ready(self, mock_time, controller):
        """首次 _emit_frame() 调用，当 time.time() >= 0.1 时应渲染。"""
        mock_time.return_value = 0.15  # 与 _last_emit_time=0.0 相差 150ms > 100ms

        controller._emit_frame()

        controller._push_frame.assert_called_once_with(["line1", "line2"])
        assert controller._last_emit_time == 0.15

    # ── 场景 2：首次调用时间 < 0.1 → 也被节流 ──

    @patch("src.tui._subagent_panel.time.time")
    def test_first_emit_throttled_when_time_too_small(self, mock_time, controller):
        """首次 _emit_frame() 在 time.time() < 0.1 时应被节流（间隔不足 100ms）。"""
        mock_time.return_value = 0.05  # 与 0.0 相差 50ms < 100ms

        controller._emit_frame()

        controller._push_frame.assert_not_called()
        assert controller._last_emit_time == 0.0  # 未被更新

    # ── 场景 3：100ms 内第二次调用 → 跳过渲染（节流生效） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_throttle_within_100ms(self, mock_time, controller):
        """100ms 内连续调用 _emit_frame()，第二次应被节流。"""
        # 第一次调用：时间 0.1（与初始 0.0 相差 100ms，刚好可渲染）
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._last_emit_time == 0.1

        # 第二次调用：时间 0.15（与 0.1 相差 50ms < 100ms）
        mock_time.return_value = 0.15
        controller._emit_frame()

        assert controller._push_frame.call_count == 1  # 未增加

    # ── 场景 4：100ms 后调用 → 正常渲染（节流窗口重置） ──

    @patch("src.tui._subagent_panel.time.time")
    def test_emit_after_interval(self, mock_time, controller):
        """间隔超过 100ms 后调用应正常渲染。"""
        # 第一次调用：时间 0.1
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._last_emit_time == 0.1

        # 第二次调用：时间 0.25（与 0.1 相差 150ms >= 100ms）
        mock_time.return_value = 0.25
        controller._emit_frame()

        assert controller._push_frame.call_count == 2
        assert controller._last_emit_time == 0.25

    # ── 场景 5：节流期间多条事件合并为一次渲染 ──

    @patch("src.tui._subagent_panel.time.time")
    def test_multiple_throttled_events_merged(self, mock_time, controller):
        """节流期间多条事件只合并为最早的一次渲染。"""
        # 首次渲染：时间 0.1
        mock_time.return_value = 0.1
        controller._emit_frame()
        assert controller._push_frame.call_count == 1
        assert controller._last_emit_time == 0.1

        # 3 次快速调用（0.12 / 0.14 / 0.16，全部在 100ms 窗口内），全部被节流
        for t in [0.12, 0.14, 0.16]:
            mock_time.return_value = t
            controller._emit_frame()

        assert controller._push_frame.call_count == 1  # 全部被节流

        # 100ms 后恢复正常渲染
        mock_time.return_value = 0.25  # 与 0.1 相差 150ms >= 100ms
        controller._emit_frame()
        assert controller._push_frame.call_count == 2

    # ── 场景 6：stop() 不经过 _emit_frame() → 不受节流影响 ──

    def test_stop_bypasses_emit_frame(self, controller):
        """stop() 应该直接调用 _push_frame，不经过 _emit_frame() 节流。"""
        controller._active = True  # stop() 需要 _active=True 才能执行清理逻辑
        with patch.object(controller, '_unregister_panel_refresh') as mock_unreg:
            with patch.object(controller, '_push_frame') as mock_push:
                controller.stop()

        # stop() 应该调用 _push_frame([]) 清除面板（不经过 _emit_frame）
        mock_push.assert_called_once_with([])

    # ── 场景 7：_last_emit_time 初始值为 0.0 ──

    def test_initial_last_emit_time_is_zero(self):
        """_last_emit_time 初始值应为 0.0。"""
        ctrl = SubAgentPanelController()
        assert ctrl._last_emit_time == 0.0

    # ── 场景 8：_EMIT_INTERVAL 类常量存在且值为 0.1 ──

    def test_emit_interval_constant(self):
        """_EMIT_INTERVAL 类常量应存在且为 0.1。"""
        assert SubAgentPanelController._EMIT_INTERVAL == 0.1
