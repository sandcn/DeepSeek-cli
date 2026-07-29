"""Tests for StreamChunkHandler base class time-based throttling."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.api.stream.handlers._base import StreamChunkHandler


class _ConcreteHandler(StreamChunkHandler):
    """Concrete subclass for testing StreamChunkHandler."""
    _EVENT_TYPE = "TestChunkEvent"
    _MIN_CHARS = 1  # 每 1 字符就 flush（确保字符节流不干扰时间节流测试）

    def handle(self, ctx, text: str, token_est: int | None = None) -> None:
        """Concrete implementation of abstract handle method."""
        self.buffer(text, getattr(ctx, 'label', None) if ctx else None)


class TestStreamChunkHandlerTimeThrottle:
    """Test time-based throttling on StreamChunkHandler."""

    # ── 场景 1：正常 flush（buffer 够了且时间够）→ 发布事件 ──

    @patch("src.api.stream.handlers._base.publish_event")
    @patch("src.api.stream.handlers._base.time.time")
    def test_normal_flush_after_interval(self, mock_time, mock_publish):
        """buffer() 在时间间隔足够后应该正常发布事件。"""
        mock_time.return_value = 0.15  # 与 last_flush_time(0.0) 相差 150ms > 100ms
        handler = _ConcreteHandler()

        handler.buffer("a", label="test")

        mock_publish.assert_called_once_with(
            "TestChunkEvent", text="a", label="test"
        )

    # ── 场景 2：时间间隔不足 → 不发布事件 ──

    @patch("src.api.stream.handlers._base.publish_event")
    @patch("src.api.stream.handlers._base.time.time")
    def test_throttle_rapid_calls(self, mock_time, mock_publish):
        """快速连续调用 buffer()，时间间隔不足时不发布事件。"""
        # 时间序列：init=0.0，第一次 flush=0.05（间隔50ms<100ms，节流）
        mock_time.side_effect = [0.0, 0.05]
        handler = _ConcreteHandler()

        handler.buffer("a", label="test")

        mock_publish.assert_not_called()

    # ── 场景 3：强制 flush（flush() 公开方法）→ 即使时间不足也发布 ──

    @patch("src.api.stream.handlers._base.publish_event")
    @patch("src.api.stream.handlers._base.time.time")
    def test_force_flush_bypasses_throttle(self, mock_time, mock_publish):
        """flush() 公开方法应跳过时间门控，强制发布事件。"""
        # 时间序列：init=0.0，第一次 buffer=0.0（间隔0<100ms，被节流）
        # 然后 flush() 强制发布
        mock_time.side_effect = [0.0, 0.0]
        handler = _ConcreteHandler()

        # 先累积 buffer 但不触发 flush（_MIN_CHARS=1，时间间隔不足节流）
        handler.buffer("a", label="test")
        mock_publish.assert_not_called()  # 被时间节流阻止

        # 强制 flush
        handler.flush("test")

        mock_publish.assert_called_once_with(
            "TestChunkEvent", text="a", label="test"
        )

    # ── 场景 4：空 buffer → 不发布事件 ──

    @patch("src.api.stream.handlers._base.publish_event")
    def test_empty_buffer_no_publish(self, mock_publish):
        """buffer 为空时，flush() 应不发布事件。"""
        handler = _ConcreteHandler()
        handler.flush("test")
        mock_publish.assert_not_called()

    # ── 场景 5：时间间隔恢复后正常发布 ──

    @patch("src.api.stream.handlers._base.publish_event")
    @patch("src.api.stream.handlers._base.time.time")
    def test_recovered_interval_normal_publish(self, mock_time, mock_publish):
        """时间间隔恢复后，buffer() 应正常发布事件。"""
        # 时间序列：
        #   init=0.0
        #   t=0.0 → buffer("a")，时间间隔 0ms < 100ms，节流
        #   t=0.15 → buffer("b")，前次 flush(last=0.0) 距今 150ms >= 100ms，发布
        mock_time.side_effect = [0.0, 0.15]
        handler = _ConcreteHandler()

        # 第一次 buffer：时间不足，节流
        handler.buffer("a", label="test")
        mock_publish.assert_not_called()

        # 第二次 buffer：时间足够（150ms），发布累积的 "ab"
        handler.buffer("b", label="test")
        mock_publish.assert_called_once_with(
            "TestChunkEvent", text="ab", label="test"
        )

    # ── 场景 6：不同 label 不影响节流（节流基于 handler 实例）──

    @patch("src.api.stream.handlers._base.publish_event")
    @patch("src.api.stream.handlers._base.time.time")
    def test_throttle_per_handler_instance(self, mock_time, mock_publish):
        """时间节流是基于 handler 实例的，不同 label 共享同一个 handler 的节流状态。"""
        # 时间序列：init=0.0，两个 buffer 都在 0.05s
        mock_time.side_effect = [0.0, 0.05, 0.05]
        handler = _ConcreteHandler()

        handler.buffer("a", label="label1")
        mock_publish.assert_not_called()  # 时间不足，节流

        handler.buffer("b", label="label2")
        mock_publish.assert_not_called()  # 仍被节流（同一 handler）

    # ── 场景 7：flush() 后重置节流计时器 ──

    @patch("src.api.stream.handlers._base.publish_event")
    @patch("src.api.stream.handlers._base.time.time")
    def test_flush_resets_throttle_timer(self, mock_time, mock_publish):
        """flush() 强制发布后，计时器被更新，后续 buffer 受新计时影响。"""
        # 时间序列：
        #   init=0.0
        #   t=0.03 → buffer("a")，时间不足，节流
        #   t=0.03 → flush() 强制发布，更新 last_flush_time=0.03
        #   t=0.08 → buffer("b")，与 last_flush_time(0.03) 相差50ms<100ms，节流
        mock_time.side_effect = [0.0, 0.03, 0.03, 0.08]
        handler = _ConcreteHandler()

        handler.buffer("a", label="test")
        mock_publish.assert_not_called()

        handler.flush("test")
        mock_publish.assert_called_once_with(
            "TestChunkEvent", text="a", label="test"
        )
        mock_publish.reset_mock()

        # 强制 flush 后计时器更新，50ms 后 buffer 仍被节流
        handler.buffer("b", label="test")
        mock_publish.assert_not_called()
