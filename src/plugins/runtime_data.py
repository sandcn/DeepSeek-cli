"""运行时数据服务插件 — 未接缝能力下沉为内核 ``ctx.*`` 服务。

「一切皆插件」：以下原为「模块级函数直接调用」的运行时能力，收敛为内核服务，
使其成为可替换/可自省的能力接入点（profile 可按需装载）：

- ``ctx.message_queue``：异步消息队列（生产者/消费者解耦）；
- ``ctx.multimodal``：多模态判定 + 图片 content blocks + 上传前图片瘦身；
- ``ctx.context_selector``：上下文选择的纯函数集合；
- ``ctx.context_summarizer``：上下文摘要生成；
- ``ctx.stats``：会话级 token 统计与速度；
- ``ctx.tokens``：token 启发式估算。

每个服务是清单中的独立插件条目（``runtime_data`` bundle），可按 Profile/Patch
禁用或替换；各方法委托底层模块（保持既有 monkeypatch/单例语义）。
"""

from __future__ import annotations

from typing import Any, List, Optional

from ..kernel import Service, plugin


class MessageQueueService(Service):
    """消息队列服务 — 占据 ``ctx.message_queue``。"""

    provide = "message_queue"
    name = "message_queue"

    def create(self):
        """创建一个新的异步消息队列。"""
        from ..core.message_queue import AsyncMessageQueue

        return AsyncMessageQueue()

    def default(self):
        if getattr(self, "_default", None) is None:
            self._default = self.create()
        return self._default


class MultimodalService(Service):
    """多模态服务 — 占据 ``ctx.multimodal``。"""

    provide = "multimodal"
    name = "multimodal"

    def is_multimodal(self, model: Optional[str]) -> bool:
        from ..core.multimodal import is_multimodal_model

        return bool(is_multimodal_model(model))

    def extract_image_refs(self, text: str):
        from ..core.multimodal import extract_image_refs

        return extract_image_refs(text)

    def build_user_content_blocks(self, *args, **kwargs):
        from ..core.multimodal import build_user_content_blocks

        return build_user_content_blocks(*args, **kwargs)

    def build_image_content_blocks(self, *args, **kwargs):
        from ..core.multimodal import build_image_content_blocks

        return build_image_content_blocks(*args, **kwargs)

    def content_to_text(self, content) -> str:
        from ..core.multimodal import content_to_text

        return content_to_text(content)

    def clear_cache(self) -> None:
        from ..core.multimodal import clear_multimodal_cache

        clear_multimodal_cache()

    def optimize_messages_for_upload(self, messages: list) -> dict:
        """在**发送副本**上执行图片折叠 + 压缩（原地修改）。"""
        from ..api.image_upload import optimize_messages_for_upload

        return optimize_messages_for_upload(messages)

    def clear_upload_cache(self) -> None:
        from ..api.image_upload import clear_upload_cache

        clear_upload_cache()


class ContextSelectorService(Service):
    """上下文选择服务 — 占据 ``ctx.context_selector``。"""

    provide = "context_selector"
    name = "context_selector"

    def _mod(self):
        from ..core import context_selector

        return context_selector

    def compute_message_stats(self, messages):
        return self._mod().compute_message_stats(messages)

    def total_chars(self, messages) -> int:
        return self._mod().total_chars(messages)

    def total_tokens(self, messages) -> int:
        return self._mod().total_tokens(messages)

    def exceeds_limit(self, total_chars_val, total_tokens_val, **_kwargs) -> bool:
        return self._mod().exceeds_limit_values(total_chars_val, total_tokens_val, **_kwargs)

    def should_auto_force(self, total_chars_val, total_tokens_val, **_kwargs) -> bool:
        return self._mod().should_auto_force_values(total_chars_val, total_tokens_val, **_kwargs)

    def usage_percent(self, total_chars_val, **_kwargs) -> float:
        return self._mod().calc_usage_percent_values(total_chars_val, **_kwargs)

    def adjust_keep_for_tool_groups(self, messages, keep_recent=None):
        return self._mod().adjust_keep_for_tool_groups(messages, keep_recent)

    def select_for_compression(self, messages, keep_recent=None, force=False,
                               total_chars_val=None, total_tokens_val=None):
        return self._mod().select_for_compression(
            messages, keep_recent, force, total_chars_val, total_tokens_val,
        )

    def message_to_text(self, message) -> str:
        return self._mod().message_to_text(message)


class ContextSummarizerService(Service):
    """上下文摘要服务 — 占据 ``ctx.context_summarizer``。"""

    provide = "context_summarizer"
    name = "context_summarizer"

    def summarize(self, messages_to_compress, has_prior_summary, summarize_fn, model):
        from ..core.context_summarizer import summarize

        return summarize(messages_to_compress, has_prior_summary, summarize_fn, model)

    def build_summary_prompt(self, messages_to_compress, has_prior_summary,
                             max_output_chars=None):
        from ..core.context_summarizer import build_summary_prompt

        return build_summary_prompt(messages_to_compress, has_prior_summary, max_output_chars)


class StatsService(Service):
    """会话统计服务 — 占据 ``ctx.stats``。"""

    provide = "stats"
    name = "stats"

    def _mod(self):
        from ..core import stats

        return stats

    def reset(self) -> None:
        self._mod().reset_stats()

    def reset_speed(self) -> None:
        self._mod().reset_token_speed()

    def accumulate_usage(self, usage: dict, increment_calls: bool = True) -> None:
        self._mod().accumulate_usage(usage, increment_calls=increment_calls)

    def add_token_size(self, size: int) -> None:
        self._mod().add_token_size(size)

    def add_token_size_batch(self, size: int, elapsed: float) -> None:
        self._mod().add_token_size_batch(size, elapsed)

    def record_generation_rate(self, size: int, elapsed: float) -> None:
        self._mod().record_generation_rate(size, elapsed)

    def adjust_token_size(self, size: int) -> None:
        self._mod().adjust_token_size(size)

    def token_stats(self) -> dict:
        return self._mod().get_token_stats()

    def session_start_time(self) -> float:
        return self._mod().get_session_start_time()

    def total_input_tokens(self) -> int:
        return self._mod().get_total_input_tokens()

    def total_output_tokens(self) -> int:
        return self._mod().get_total_output_tokens()

    def total_tokens(self) -> int:
        return self._mod().get_total_tokens()

    def token_speed(self) -> float:
        return self._mod().get_token_speed()

    def speed_snapshot(self) -> dict:
        return self._mod().get_token_speed_snapshot()

    def set_stream_speed(self, speed: float) -> None:
        self._mod().set_stream_speed(speed)

    def set_tool_parse_elapsed(self, elapsed: float) -> None:
        self._mod().set_tool_parse_elapsed(elapsed)


class TokensService(Service):
    """Token 估算服务 — 占据 ``ctx.tokens``。"""

    provide = "tokens"
    name = "tokens"

    def estimate(self, text) -> int:
        from ..core.tokens import estimate_tokens

        return estimate_tokens(text)


@plugin("message_queue", provide=["message_queue"])
def apply_message_queue(ctx):
    return MessageQueueService(ctx, ctx.config)


@plugin("multimodal", provide=["multimodal"])
def apply_multimodal(ctx):
    return MultimodalService(ctx, ctx.config)


@plugin("context_selector", provide=["context_selector"])
def apply_context_selector(ctx):
    return ContextSelectorService(ctx, ctx.config)


@plugin("context_summarizer", provide=["context_summarizer"])
def apply_context_summarizer(ctx):
    return ContextSummarizerService(ctx, ctx.config)


@plugin("stats", provide=["stats"])
def apply_stats(ctx):
    return StatsService(ctx, ctx.config)


@plugin("tokens", provide=["tokens"])
def apply_tokens(ctx):
    return TokensService(ctx, ctx.config)


__all__ = [
    "MessageQueueService",
    "MultimodalService",
    "ContextSelectorService",
    "ContextSummarizerService",
    "StatsService",
    "TokensService",
    "apply_message_queue",
    "apply_multimodal",
    "apply_context_selector",
    "apply_context_summarizer",
    "apply_stats",
    "apply_tokens",
]
