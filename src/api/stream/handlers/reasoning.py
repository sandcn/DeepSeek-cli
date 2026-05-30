"""ReasoningHandler — 处理 reasoning_content 增量

终端渲染已移至 ChatUIConsumer（chat_ui.py），通过 DisplayEventBus
的 ReasoningChunkEvent 事件驱动。此处仅负责：
- 累积 reasoning_full 用于最终结果
- 发布 ModelPhaseEvent（首次进入 thinking 阶段时）
- 发布 ReasoningChunkEvent 到 EventBus（由 ChatUIConsumer 消费）
"""
from __future__ import annotations
from ...events import publish_event
from ...tokens import estimate_tokens
from ..context import StreamContext
from ._base import StreamChunkHandler


class ReasoningHandler(StreamChunkHandler):
    """处理流式 reasoning_content chunk"""

    _EVENT_TYPE = "ReasoningChunkEvent"
    _MIN_CHARS = 1

    def handle(self, ctx: StreamContext, rc: str, token_est: int | None = None) -> None:
        """处理一段 reasoning_content 增量"""
        if ctx.display and ctx.label and not ctx.phase_thinking_sent:
            ctx.display.update_model_phase(ctx.label, "thinking")
            ctx.phase_thinking_sent = True
            # 🔥 SubAgent（silent=True）时额外发布 ModelPhaseEvent 到 EventBus
            if ctx.silent:
                publish_event("ModelPhaseEvent",
                              label=ctx.label, phase="thinking",
                              info="", source=ctx.label or "")

        ctx.reasoning_full += rc

        ctx.token_estimate += token_est if token_est is not None else estimate_tokens(rc)
        ctx._live_total_dirty = True

        # 🔥 发布推理 chunk 事件到 EventBus
        # - ChatUIConsumer 消费此事件驱动终端 Markdown 渲染
        # - WebUI 桥接器消费此事件驱动 SSE 推送
        # 带文本累积节流：短 chunk 合并，减少 EventBus 压力
        # 注意：即使 silent=True 也要发布到 EventBus，确保 WebUI 能显示 SubAgent 流式推理内容
        self.buffer(rc, ctx.label)

        ctx.speed_chunk_count += 1


