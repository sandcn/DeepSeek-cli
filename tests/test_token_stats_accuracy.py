"""token 统计与上下文百分比口径准确性测试。

覆盖修复（问题：统计 tok 与显示 tok 百分比不准）：

1. **逐 delta 估算系统性高估**：流式对每个 delta 分别调用
   ``estimate_tokens`` 再求和，``max(1, ...)`` 下限使短分片（英文 1-3 字符）
   每片至少 1 token → 英文文本可高估 3 倍以上。修复为「字符分类增量累加 +
   ``estimate_tokens_from_counts`` 还原整体估算」，与对累计全文调用
   ``estimate_tokens`` 数值完全一致。

2. **状态栏「总tok」/速度分子口径不一**：流式路径只累加估算且从不用真实
   usage 修正，非流式路径累加真实 output。修复为真实 usage 到达时
   ``adjust_token_size(真实 - 已累加估算)`` 修正（``StreamContext.
   apply_real_usage``）。

3. **上下文百分比流式与结束口径不一（跳变）**：流式增量含 reasoning
   （不回传）且用逐块估算，消息追加后 ``MessageStatsCache`` 只按 content +
   截断工具参数的整体估算 → 流式期间虚高、结束后回落。修复为
   ``streamed_output_tokens`` 只含 content 的整体估算，与消息口径一致。
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.tokens import (
    count_cjk_other,
    estimate_tokens,
    estimate_tokens_from_counts,
)


@pytest.fixture(autouse=True)
def _clean_stats():
    """清理全局统计单例与上下文全局快照（测试间隔离）。"""
    from src.core.stats import reset_stats, reset_token_speed
    from src.core.context_manager import (
        set_context_usage_percent, set_streaming_extra_tokens,
        set_active_context_manager,
    )
    reset_stats()
    reset_token_speed(keep_total=False)
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)
    yield
    reset_stats()
    reset_token_speed(keep_total=False)
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)


# ═══════════════════════════════════════════════════════════════
# 1. 字符分类估算与整体 estimate_tokens 一致
# ═══════════════════════════════════════════════════════════════

class TestEstimateFromCounts:

    @pytest.mark.parametrize("text", [
        "",
        "hello world",
        "中文测试内容",
        "mixed 中文 and English 123",
        "a",
        "中文",
        "!@#$%^&*()",
        "日本語とハングル 한국어",
    ])
    def test_matches_whole_estimate(self, text):
        cjk, other = count_cjk_other(text)
        assert estimate_tokens_from_counts(cjk, other) == estimate_tokens(text)

    def test_incremental_accumulation_equals_whole(self):
        """逐片增量累加分类计数 → 整体估算，与全文 estimate_tokens 一致。"""
        text = "Hello world 这是一个测试，用来验证增量估算与整体估算是否一致。" * 5
        cjk = other = 0
        for i in range(0, len(text), 3):
            c, o = count_cjk_other(text[i:i + 3])
            cjk += c
            other += o
        assert estimate_tokens_from_counts(cjk, other) == estimate_tokens(text)

    def test_empty_counts_zero(self):
        assert estimate_tokens_from_counts(0, 0) == 0


# ═══════════════════════════════════════════════════════════════
# 2. 逐 delta 求和高估 → 整体估算修复
# ═══════════════════════════════════════════════════════════════

class TestNoChunkedOverestimate:

    def test_chunked_sum_overestimates_ascii(self):
        """逐字符分别估算求和显著大于整体估算（旧口径缺陷，作为对照）。"""
        text = "This is an English sentence for token estimation. " * 10
        chunked = sum(estimate_tokens(c) for c in text)
        whole = estimate_tokens(text)
        assert chunked > whole * 2

    def test_incremental_matches_whole_not_chunked(self):
        """增量口径结果等于整体估算，且小于逐块求和。"""
        text = "This is an English sentence for token estimation. " * 10
        cjk = other = 0
        for c in text:
            a, b = count_cjk_other(c)
            cjk += a
            other += b
        incremental = estimate_tokens_from_counts(cjk, other)
        assert incremental == estimate_tokens(text)
        assert incremental < sum(estimate_tokens(c) for c in text)


# ═══════════════════════════════════════════════════════════════
# 3. StreamContext 估算口径
# ═══════════════════════════════════════════════════════════════

class TestStreamContextEstimate:

    def _ctx(self, label="assistant"):
        from src.api.stream.context import StreamContext
        return StreamContext("m", None, label, True)

    def test_content_estimate_matches_whole(self):
        ctx = self._ctx()
        text = "content 内容 hello world"
        ctx.add_content_delta(text)
        assert ctx.token_estimate == estimate_tokens(text)
        # 上下文占用口径：只含 content
        assert ctx.streamed_output_tokens == estimate_tokens(text)

    def test_token_estimate_includes_reasoning_but_streamed_excludes(self):
        """token_estimate 含 reasoning（供 output 统计）；streamed 只含 content。"""
        ctx = self._ctx()
        r, c = "reasoning 推理内容", "answer 回答内容"
        ctx.add_reasoning_delta(r)
        assert ctx.streamed_output_tokens == 0        # 仅 reasoning 时上下文增量 0
        ctx.add_content_delta(c)
        assert ctx.token_estimate == estimate_tokens(r) + estimate_tokens(c)
        assert ctx.streamed_output_tokens == estimate_tokens(c)

    def test_args_in_token_estimate_only(self):
        ctx = self._ctx()
        ctx.add_args_delta('{"path": "a.txt"}')
        assert ctx.token_estimate == estimate_tokens('{"path": "a.txt"}')
        assert ctx.streamed_output_tokens == 0

    def test_incremental_chunks_equal_whole(self):
        """分片累加 == 整体估算（内容/推理分开累加后各自独立成立）。"""
        ctx = self._ctx()
        text = "分片输出内容 incremental chunk estimate test " * 4
        for i in range(0, len(text), 4):
            ctx.add_content_delta(text[i:i + 4])
        assert ctx.token_estimate == estimate_tokens(text)
        assert ctx.streamed_output_tokens == estimate_tokens(text)


# ═══════════════════════════════════════════════════════════════
# 4. 真实 usage 修正全局总 tok（状态栏 / 速度分子）
# ═══════════════════════════════════════════════════════════════

class TestRealUsageCorrection:

    def test_apply_real_usage_replaces_estimate(self):
        from src.core.stats import get_total_tokens
        from src.api.stream.context import StreamContext
        ctx = StreamContext("m", None, "assistant", True)
        text = "a" * 1000  # 估算 int(300) 偏高，真实 output 设 250
        ctx.add_content_delta(text)
        est = ctx.stream_added_tokens
        assert get_total_tokens() == est
        ctx.apply_real_usage(250)
        assert get_total_tokens() == 250
        assert ctx.stream_added_tokens == 250

    def test_apply_real_usage_zero_clamps(self):
        from src.core.stats import get_total_tokens
        from src.api.stream.context import StreamContext
        ctx = StreamContext("m", None, "assistant", True)
        ctx.add_content_delta("a" * 500)
        ctx.apply_real_usage(0)
        assert get_total_tokens() == 0

    def test_adjust_token_size_negative_and_clamp(self):
        from src.core.stats import add_token_size, adjust_token_size, get_total_tokens
        add_token_size(100)
        adjust_token_size(-30)
        assert get_total_tokens() == 70
        adjust_token_size(-1000)          # 钳制到 0，绝不为负
        assert get_total_tokens() == 0
        adjust_token_size(50)
        assert get_total_tokens() == 50

    def test_adjust_invalid_ignored(self):
        from src.core.stats import add_token_size, adjust_token_size, get_total_tokens
        add_token_size(10)
        adjust_token_size(0)
        adjust_token_size(None)
        adjust_token_size("abc")
        assert get_total_tokens() == 10


# ═══════════════════════════════════════════════════════════════
# 5. pipeline 集成：状态栏总tok / 会话统计 == 真实 usage
# ═══════════════════════════════════════════════════════════════

def _chunk(content=None, reasoning=None, usage=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    choice = {"delta": delta} if delta else {}
    out = {"choices": [choice] if choice else []}
    if usage is not None:
        out["usage"] = usage
    return out


async def _run_pipeline(ctx, chunks):
    from src.api.stream.pipeline_async import AsyncStreamPipeline
    pipeline = AsyncStreamPipeline()

    async def gen():
        for ch in chunks:
            yield ch

    return await pipeline.process(ctx, gen(), silent=True)


class TestPipelineIntegration:

    @pytest.mark.asyncio
    async def test_total_tokens_corrected_to_real_usage(self):
        from src.core.stats import get_total_tokens, get_token_stats
        from src.api.stream.context import StreamContext
        ctx = StreamContext("m", None, "assistant", True)
        text = "这是一个测试，用来验证 token 统计是否准确。Hello world this is a test."
        chunks = [_chunk(content=c) for c in text]
        chunks.append(_chunk(usage={"prompt_tokens": 100, "completion_tokens": 25}))
        await _run_pipeline(ctx, chunks)
        assert get_total_tokens() == 25
        assert get_token_stats()["output"] == 25

    @pytest.mark.asyncio
    async def test_total_tokens_without_usage_is_whole_estimate(self):
        """无 usage（中断等）：总 tok 为整体估算（非逐块高估）。"""
        from src.core.stats import get_total_tokens
        from src.api.stream.context import StreamContext
        ctx = StreamContext("m", None, "assistant", True)
        text = "This is an English sentence for token estimation. " * 10
        chunks = [_chunk(content=c) for c in text]
        await _run_pipeline(ctx, chunks)
        assert get_total_tokens() == estimate_tokens(text)
        assert get_total_tokens() < sum(estimate_tokens(c) for c in text)

    @pytest.mark.asyncio
    async def test_reasoning_and_content_total_matches_whole(self):
        from src.core.stats import get_total_tokens
        from src.api.stream.context import StreamContext
        ctx = StreamContext("deepseek-reasoner", None, "assistant", True)
        reason = "推理过程 reasoning content " * 3
        content = "最终回答 final answer " * 3
        chunks = ([_chunk(reasoning=c) for c in reason]
                  + [_chunk(content=c) for c in content])
        await _run_pipeline(ctx, chunks)
        assert get_total_tokens() == estimate_tokens(reason) + estimate_tokens(content)

    @pytest.mark.asyncio
    async def test_percent_stream_and_after_append_consistent(self):
        """流式增量与消息追加后口径一致（不回落跳变）。"""
        from src.core.context_manager import (
            ContextManager, get_context_usage_percent,
            update_streaming_usage,
        )
        from src.core.adapters.config import MockConfigAdapter
        from src.api.stream.context import StreamContext

        msgs = [{"role": "system", "content": "s" * 100}]
        cfg = MockConfigAdapter({"model_context_tokens": 10000})
        cm = ContextManager(msgs, "m", config_port=cfg)
        ctx = StreamContext("m", None, "assistant", True)
        text = "回答内容：验证上下文百分比在流式期间与结束后的口径一致。"
        chunks = [_chunk(content=c) for c in text]
        await _run_pipeline(ctx, chunks)
        # 模拟流式期间最后一次刷新（清零前）
        update_streaming_usage(ctx.streamed_output_tokens, "assistant")
        pct_stream = get_context_usage_percent()
        # 流式结束清零 + 消息追加（真实链路）
        update_streaming_usage(0, "assistant")
        msgs.append({"role": "assistant", "content": ctx.content_full})
        cm.refresh_usage()
        pct_after = get_context_usage_percent()
        assert pct_stream == pct_after
        # 且流式增量等于该消息的上下文统计
        assert ctx.streamed_output_tokens == estimate_tokens(ctx.content_full)

    @pytest.mark.asyncio
    async def test_streamed_output_tokens_monotonic_after_usage(self):
        from src.api.stream.context import StreamContext
        ctx = StreamContext("m", None, "assistant", True)
        chunks = [
            _chunk(content="a" * 500),
            _chunk(content="b" * 500, usage={"prompt_tokens": 10, "completion_tokens": 50}),
            _chunk(content="c" * 500),
        ]
        await _run_pipeline(ctx, chunks)
        # usage 后继续 content：上下文增量仍随 content 单调增长（不被覆盖成小值）
        assert ctx.streamed_output_tokens == estimate_tokens("a" * 500 + "b" * 500 + "c" * 500)
