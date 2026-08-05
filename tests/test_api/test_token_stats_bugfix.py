"""/cost token 统计修复回归测试。

背景：/cost 命令展示 get_token_stats() 的 input/output/calls，此前存在
三类统计错误导致「输入 tok 统计错误」：
1. SubAgent._update_display 在模型调用层已累计 usage 后再次 accumulate_usage，
   导致 SubAgent 每次模型调用的 input/output/calls 全部翻倍。
2. AsyncStreamPipeline._handle_usage 将「output 修正」与「input 真实值」
   拆分为两次 accumulate_usage，导致 calls 每次流式调用多计 1 次。
3. SpeedHandler 实时 token 估计累计（非真实 API 调用）也 +1 calls，
   导致 /cost 调用次数虚高。

修复后约束：
- accumulate_usage(..., increment_calls=True)：真实 API 调用（默认），calls +1
- accumulate_usage(..., increment_calls=False)：实时估计累计，calls 不变
- _handle_usage 合并为单次累计：input/output/calls 三者精确
- SubAgent._update_display 仅更新显示层，不再重复累计
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.api.stats import accumulate_usage, get_token_stats, reset_stats
from src.api.stream.context import StreamContext
from src.api.stream.handlers.speed import SpeedHandler
from src.api.stream.pipeline_async import AsyncStreamPipeline
from src.core.subagent import SubAgent


@pytest.fixture(autouse=True)
def _reset_stats():
    reset_stats()
    yield
    reset_stats()


class TestAccumulateUsageCallsFlag:
    """accumulate_usage 的 increment_calls 参数语义。"""

    def test_real_call_increments_calls(self):
        accumulate_usage({"input": 100, "output": 200})
        stats = get_token_stats()
        assert stats["input"] == 100
        assert stats["output"] == 200
        assert stats["calls"] == 1

    def test_live_estimate_no_calls(self):
        accumulate_usage({"input": 0, "output": 50}, increment_calls=False)
        stats = get_token_stats()
        assert stats["output"] == 50
        assert stats["calls"] == 0

    def test_mixed_live_and_real(self):
        # 流式过程中实时估计累计若干次 + 一次真实调用
        accumulate_usage({"input": 0, "output": 30}, increment_calls=False)
        accumulate_usage({"input": 0, "output": 20}, increment_calls=False)
        accumulate_usage({"input": 1000, "output": 0})
        stats = get_token_stats()
        assert stats["input"] == 1000
        assert stats["output"] == 50
        assert stats["calls"] == 1


class TestSpeedHandlerLiveAccumulate:
    """SpeedHandler 实时 token 估计累计不污染 calls。"""

    def test_do_accumulate_does_not_increment_calls(self):
        ctx = StreamContext("deepseek", None, "main", False)
        ctx.content_full = "hello"
        ctx.token_estimate = 120
        ctx.last_live_est = 0
        handler = SpeedHandler()
        handler._do_accumulate(ctx)
        stats = get_token_stats()
        assert stats["output"] == 120
        assert stats["calls"] == 0
        assert ctx.last_live_est == 120


class TestPipelineHandleUsage:
    """_handle_usage 合并为单次累计：calls 恰 +1，input/output 精确。"""

    def test_handle_usage_accumulates_once(self):
        ctx = StreamContext("deepseek", None, "main", False)
        ctx.content_full = "hello"
        # ── 流式过程：SpeedHandler 已实时累计估计 output=50（calls 不变）──
        handler = SpeedHandler()
        ctx.token_estimate = 50
        ctx.last_live_est = 0
        handler._do_accumulate(ctx)
        assert get_token_stats()["output"] == 50
        assert get_token_stats()["calls"] == 0

        # ── usage chunk 到达：真实值修正，input 加真实值，calls 恰 +1 ──
        pipeline = AsyncStreamPipeline()
        chunk = {"usage": {"prompt_tokens": 1000, "completion_tokens": 150}}
        pipeline._handle_usage(ctx, chunk)

        stats = get_token_stats()
        assert stats["input"] == 1000
        assert stats["output"] == 150  # 50 估计 + (150-50) 修正 = 150
        assert stats["calls"] == 1
        assert ctx.final_usage_received is True
        assert ctx.usage_accumulated is True

    def test_handle_usage_without_prior_live_est(self):
        """无 SpeedHandler 实时累计时，output 直接加真实值，calls 恰 +1。"""
        ctx = StreamContext("deepseek", None, "main", False)
        pipeline = AsyncStreamPipeline()
        chunk = {"usage": {"prompt_tokens": 500, "completion_tokens": 80}}
        pipeline._handle_usage(ctx, chunk)
        stats = get_token_stats()
        assert stats["input"] == 500
        assert stats["output"] == 80
        assert stats["calls"] == 1


class TestSubAgentUpdateDisplay:
    """SubAgent._update_display 仅更新显示层，不再重复累计 token。"""

    def _make_subagent(self, display=None):
        sub = object.__new__(SubAgent)
        sub.label = "agent-1"
        sub.display = display
        sub._event_port = MagicMock()
        return sub

    def test_update_display_does_not_accumulate_without_display(self):
        sub = self._make_subagent(display=None)
        sub._update_display({"input": 100, "output": 200})
        stats = get_token_stats()
        assert stats["input"] == 0
        assert stats["output"] == 0
        assert stats["calls"] == 0
        # 事件仍发布（UsageUpdated + ModelPhase）
        assert sub._event_port.publish_event.call_count == 2

    def test_update_display_does_not_accumulate_with_display(self):
        display = MagicMock()
        sub = self._make_subagent(display=display)
        sub._update_display({"input": 100, "output": 200})
        stats = get_token_stats()
        assert stats["input"] == 0
        assert stats["output"] == 0
        assert stats["calls"] == 0
        # 显示层更新仍执行
        display.update_usage.assert_called_once_with(
            "agent-1", {"input": 100, "output": 200}, replace=False,
        )
        display.update_model_phase.assert_called_once_with("agent-1", "")
