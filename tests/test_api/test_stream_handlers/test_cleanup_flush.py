"""收尾时序回归测试 — _cleanup_display 最后一批必发布且先于 PhaseDone（步骤 4）。

覆盖：
- reasoning-only 流（有推理、无 content、无工具）收尾必发布 PhaseDone("reasoning")
  （修正 phase_thinking_sent 守卫误用；此前从不发布 → close_reasoning 不执行）
- flush 尾部 token 事件先于 PhaseDoneEvent（同批事件顺序）
- ContentHandler 已发布 PhaseDone("reasoning") 后 cleanup 不重复发布（助手去重）

使用真实 DisplayEventBus（reset_default 隔离，finally 恢复）+ 全事件记录订阅者，
避免多路径 patch 脆弱性。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.api.stream.context import StreamContext
from src.api.stream.handlers.content import ContentHandler
from src.api.stream.pipeline_async import AsyncStreamPipeline
from src.tui.events.event_bus import DisplayEventBus
from src.tui.events.event_types import (
    ContentChunkEvent,
    PhaseDoneEvent,
    ReasoningChunkEvent,
)


def _record_subscriber(events: list):
    """返回订阅所有事件的记录器（event_type=None → 全事件订阅）。"""
    def _on_event(event):
        events.append(event)
    return _on_event


@pytest.fixture
def isolated_bus():
    """真实 DisplayEventBus 单例隔离（测试结束后 reset_default 恢复）。"""
    DisplayEventBus.reset_default()
    bus = DisplayEventBus.get_default()
    yield bus
    DisplayEventBus.reset_default()


class TestCleanupFlush:
    """_cleanup_display 收尾时序。"""

    @pytest.mark.asyncio
    async def test_cleanup_publishes_phase_done_reasoning_for_reasoning_only(self, isolated_bus):
        """reasoning-only 流收尾发布 PhaseDone("reasoning") 恰一次（修正守卫误用）。

        phase_thinking_sent=True 模拟 reasoning.py 已宣布 thinking 阶段（与
        PhaseDone 发布无关）；此前误用该字段导致 reasoning-only 流从不发布
        PhaseDone("reasoning")。
        """
        events: list = []
        isolated_bus.subscribe(_record_subscriber(events), event_type=None)

        ctx = StreamContext("m", None, "main", False)
        ctx.reasoning_full = "thinking..."
        ctx.phase_thinking_sent = True
        ctx.tracker.finalize = AsyncMock()

        pipeline = AsyncStreamPipeline()
        # 模拟节流滞留：100ms 时间门控未到的最后推理 token
        pipeline._reasoning_handler._chunk_buffer = "tail"

        await pipeline._cleanup_display(ctx)

        reasoning_chunks = [
            e for e in events
            if isinstance(e, ReasoningChunkEvent) and e.text == "tail"
        ]
        reasoning_done = [
            e for e in events
            if isinstance(e, PhaseDoneEvent) and e.phase == "reasoning"
        ]
        assert reasoning_chunks, "推理尾部 token 应被 flush 发布"
        assert len(reasoning_done) == 1, (
            "reasoning-only 流收尾应发布 PhaseDone('reasoning') 恰一次"
        )
        tail_idx = next(
            i for i, e in enumerate(events)
            if isinstance(e, ReasoningChunkEvent) and e.text == "tail"
        )
        done_idx = next(
            i for i, e in enumerate(events)
            if isinstance(e, PhaseDoneEvent) and e.phase == "reasoning"
        )
        assert tail_idx < done_idx, "尾部 token 事件应先于 PhaseDone('reasoning')"

    @pytest.mark.asyncio
    async def test_cleanup_flush_tail_before_phase_done(self, isolated_bus):
        """内容尾部 token flush 事件先于 PhaseDone("content")。"""
        events: list = []
        isolated_bus.subscribe(_record_subscriber(events), event_type=None)

        ctx = StreamContext("m", None, "main", False)
        ctx.content_full = "answer..."
        ctx.tracker.finalize = AsyncMock()

        pipeline = AsyncStreamPipeline()
        pipeline._content_handler._chunk_buffer = "tail"

        await pipeline._cleanup_display(ctx)

        content_chunks = [
            e for e in events
            if isinstance(e, ContentChunkEvent) and e.text == "tail"
        ]
        content_done = [
            e for e in events
            if isinstance(e, PhaseDoneEvent) and e.phase == "content"
        ]
        assert content_chunks, "内容尾部 token 应被 flush 发布"
        assert len(content_done) == 1
        tail_idx = next(
            i for i, e in enumerate(events)
            if isinstance(e, ContentChunkEvent) and e.text == "tail"
        )
        done_idx = next(
            i for i, e in enumerate(events)
            if isinstance(e, PhaseDoneEvent) and e.phase == "content"
        )
        assert tail_idx < done_idx, "尾部 token 事件应先于 PhaseDone('content')"

    @pytest.mark.asyncio
    async def test_cleanup_reasoning_done_not_duplicated(self, isolated_bus):
        """ContentHandler 已发布 PhaseDone("reasoning") 后 cleanup 不重复发布。"""
        events: list = []
        isolated_bus.subscribe(_record_subscriber(events), event_type=None)

        ctx = StreamContext("m", None, "main", False)
        ctx.reasoning_full = "thinking..."
        ctx.tracker.finalize = AsyncMock()

        # 先经 ContentHandler.handle 发布 PhaseDone("reasoning")（置位去重标志）
        handler = ContentHandler()
        handler.handle(ctx, "first content")

        pipeline = AsyncStreamPipeline()
        await pipeline._cleanup_display(ctx)

        reasoning_done = [
            e for e in events
            if isinstance(e, PhaseDoneEvent) and e.phase == "reasoning"
        ]
        assert len(reasoning_done) == 1, (
            "cleanup 不应重复发布 PhaseDone('reasoning')（助手去重）"
        )
