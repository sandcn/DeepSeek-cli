"""ContentHandler — 处理 content 增量

终端渲染已移至 ChatUIConsumer（chat_ui.py），通过 DisplayEventBus
的 ContentChunkEvent 事件驱动。此处仅负责：
- 累积 content_full 用于最终结果
- 发布 PhaseDoneEvent("reasoning") 标记推理阶段结束
- 发布 ModelPhaseEvent（首次进入 answering 阶段时）
- 发布 ContentChunkEvent 到 EventBus（由 ChatUIConsumer 消费）
"""
from __future__ import annotations
from ...events import publish_event
from ...tokens import estimate_tokens
from ..context import StreamContext
from ._base import StreamChunkHandler


class ContentHandler(StreamChunkHandler):
    """处理流式 content chunk"""

    _EVENT_TYPE = "ContentChunkEvent"
    _MIN_CHARS = 1

    def handle(self, ctx: StreamContext, dc: str, token_est: int | None = None) -> None:
        """处理一段 content 增量"""
        if ctx.is_reasoning:
            ctx.is_reasoning = False
            # 🔥 推理阶段结束 → 发布 PhaseDoneEvent（去重助手，每流恰一次）
            # 即使 silent=True 也要发布，确保 EventBus 时序正确：
            # 如果推后到 _cleanup_display() 才发布，ContentChunkEvent
            # 已经通过 buffer→flush 提前到达前端，导致"思考"气泡完成信号
            # 晚于内容数据到达，前端可能将内容渲染到错误的容器中。
            ctx.publish_phase_done_once("reasoning")

        if ctx.display and ctx.label and not ctx.phase_answering_sent:
            ctx.display.update_model_phase(ctx.label, "answering")
            ctx.phase_answering_sent = True
            # 🔥 SubAgent（silent=True）时额外发布 ModelPhaseEvent 到 EventBus
            if ctx.silent:
                publish_event("ModelPhaseEvent",
                              label=ctx.label, phase="answering",
                              info="", source=ctx.label or "")

        ctx.token_estimate += token_est if token_est is not None else estimate_tokens(dc)
        ctx.content_full += dc
        ctx.speed_chunk_count += 1
        ctx._live_total_dirty = True

        # 🔥 发布内容 chunk 事件到 EventBus
        # - ChatUIConsumer 消费此事件驱动终端 Markdown 渲染
        # 带文本累积节流：短 chunk 合并，减少 EventBus 压力
        # 注意：即使 silent=True 也要发布到 EventBus，确保流式内容不丢失
        self.buffer(dc, ctx.label)


