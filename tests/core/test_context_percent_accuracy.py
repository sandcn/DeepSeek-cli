"""main 上下文百分比统计准确性测试。

覆盖 2026-10「main 上下文百分比统计不准」三项修复：

1. **中文 token 估算系数**：原 2.5 token/字符（虚高约 3~4 倍）修正为
   DeepSeek 官方比例 0.6（中文字符）/ 0.3（其他字符），模式行
   ``main · N%`` 不再系统性虚高。
2. **真实 prompt token 基线**：服务端 ``usage.prompt_tokens``（真实输入
   token，含系统提词 + 工具列表 + 全部消息 + 模板开销）作为权威基线，
   百分比 = 基线 + 新增消息估算 + 流式增量；消息前缀变化 / 工具或模型
   变化时基线自动失效，回退纯估算口径。
3. **缓存同步检测**：``MessageStatsCache.is_synced`` 长度 + 对象身份双重
   校验，条数不变而消息被整体替换时也能发现失步并重算。
"""

from __future__ import annotations

import json

import pytest

from src.core.adapters.config import MockConfigAdapter
from src.core.context_manager import (
    ContextManager,
    get_context_usage_percent,
    set_active_context_manager,
    set_context_usage_percent,
    set_streaming_extra_tokens,
    update_real_prompt_usage,
    update_streaming_usage,
)
from src.core.internal.shared._message_stats_cache import MessageStatsCache
from src.core.tokens import (
    CJK_TOKENS_PER_CHAR,
    OTHER_TOKENS_PER_CHAR,
    estimate_tokens,
)


@pytest.fixture(autouse=True)
def _clean_globals():
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)
    yield
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)


def _cm(messages, ctx_tokens=10000, tools=None):
    cfg = MockConfigAdapter({"model_context_tokens": ctx_tokens})
    return ContextManager(messages, "m", config_port=cfg, tools=tools)


# ═══════════════════════════════════════════════════════════════
# 1. 中文 token 估算系数（DeepSeek 官方比例）
# ═══════════════════════════════════════════════════════════════

class TestCjkCoefficient:

    def test_constants_match_deepseek_official(self):
        assert CJK_TOKENS_PER_CHAR == 0.6
        assert OTHER_TOKENS_PER_CHAR == 0.3

    def test_pure_cjk(self):
        assert estimate_tokens("中" * 1000) == 600
        assert estimate_tokens("中" * 10000) == 6000

    def test_pure_ascii(self):
        assert estimate_tokens("a" * 1000) == 300

    def test_mixed(self):
        # 100 中文（60 tok）+ 1000 ASCII（300 tok）
        text = "中" * 100 + "a" * 1000
        assert estimate_tokens(text) == 360

    def test_no_longer_overestimates_cjk(self):
        """修复前 2.5 token/字符 → 1000 字 2500 token；现 600。"""
        assert estimate_tokens("中" * 1000) < 2500


# ═══════════════════════════════════════════════════════════════
# 2. 真实 prompt token 基线
# ═══════════════════════════════════════════════════════════════

class TestRealPromptBaseline:

    def test_baseline_is_authoritative(self):
        """写入真实基线后百分比 = 基线 / 窗口（不再用估算）。"""
        msgs = [{"role": "system", "content": "s" * 3000}]  # 估算 900
        cm = _cm(msgs, ctx_tokens=10000)
        assert get_context_usage_percent() == 9.0  # 纯估算
        cm.set_prompt_baseline(1234)
        assert get_context_usage_percent() == 12.3

    def test_tail_messages_added_on_top(self):
        """基线生效后新增消息叠加估算增量。"""
        msgs = [{"role": "system", "content": "s" * 3000}]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(1000)
        assert get_context_usage_percent() == 10.0
        msgs.append({"role": "user", "content": "a" * 1000})  # 300 tok
        cm.refresh_usage()
        assert get_context_usage_percent() == 13.0
        msgs.append({"role": "assistant", "content": "中" * 1000})  # 600 tok
        cm.refresh_usage()
        assert get_context_usage_percent() == 19.0

    def test_streaming_extra_added_on_top_of_baseline(self):
        """流式增量叠加在真实基线之上（AI 生成时实时上升）。"""
        msgs = [{"role": "system", "content": "s" * 3000}]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(1000)
        update_streaming_usage(500, "assistant")
        assert get_context_usage_percent() == 15.0
        update_streaming_usage(0, "assistant")
        assert get_context_usage_percent() == 10.0

    def test_baseline_invalidated_on_replacement(self):
        """基线前缀消息被替换（对象身份变化）→ 基线失效、回退估算。"""
        msgs = [{"role": "system", "content": "s" * 3000}]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(1000)
        assert get_context_usage_percent() == 10.0
        msgs[0] = {"role": "system", "content": "x" * 3000}  # 900 tok
        cm.refresh_usage()
        assert get_context_usage_percent() == 9.0  # 回退估算（非 10.0）

    def test_baseline_invalidated_on_shrinking_below_base(self):
        """消息数少于基线长度（删除基线消息）→ 基线失效。"""
        msgs = [
            {"role": "system", "content": "s" * 1000},
            {"role": "user", "content": "u" * 1000},
        ]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(500)
        assert get_context_usage_percent() == 5.0
        msgs.pop()  # 删除基线内消息
        cm.refresh_usage()
        assert get_context_usage_percent() == 3.0  # 回退估算（1000 ASCII → 300）

    def test_baseline_invalidated_on_set_tools(self):
        """工具列表变化 → 旧基线（含旧工具 schemas）失效 → 回退估算含新工具。"""
        msgs = [{"role": "system", "content": "s" * 1000}]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(1000)
        assert get_context_usage_percent() == 10.0
        tools = [{"type": "function", "function": {"name": "bash"}}]
        cm.set_tools(tools)
        expected = estimate_tokens("s" * 1000) + estimate_tokens(
            json.dumps(tools[0], ensure_ascii=False))
        assert get_context_usage_percent() == round(expected / 10000 * 100, 1)

    def test_baseline_invalidated_on_model_change(self):
        msgs = [{"role": "system", "content": "s" * 1000}]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(5000)
        assert get_context_usage_percent() == 50.0
        cm.update_model("other-model")
        assert get_context_usage_percent() == 3.0  # 回退估算

    def test_baseline_ignores_nonpositive(self):
        msgs = [{"role": "system", "content": "s" * 1000}]
        cm = _cm(msgs, ctx_tokens=10000)
        cm.set_prompt_baseline(0)
        cm.set_prompt_baseline(None)
        cm.set_prompt_baseline("bad")
        assert get_context_usage_percent() == 3.0  # 保持估算

    def test_update_real_prompt_usage_skips_subagent(self):
        """SubAgent 真实 prompt 不计入主 Agent 基线（label 前缀跳过）。"""
        msgs = [{"role": "system", "content": "s" * 1000}]
        _cm(msgs, ctx_tokens=10000)  # 注册为活跃实例
        assert get_context_usage_percent() == 3.0
        update_real_prompt_usage(9000, "agent-1")
        update_real_prompt_usage(9000, "sa-abc")
        assert get_context_usage_percent() == 3.0
        update_real_prompt_usage(5000, "assistant")
        assert get_context_usage_percent() == 50.0

    def test_update_real_prompt_usage_skips_internal_utility(self):
        """内部工具调用（压缩摘要 label="summarize"）不污染主 Agent 基线。"""
        msgs = [{"role": "system", "content": "s" * 1000}]
        _cm(msgs, ctx_tokens=10000)
        assert get_context_usage_percent() == 3.0
        update_real_prompt_usage(9000, "summarize")
        assert get_context_usage_percent() == 3.0

    def test_update_real_prompt_usage_accepts_none_label(self):
        """label=None（非 TUI/缺省主 Agent 路径）计入基线。"""
        msgs = [{"role": "system", "content": "s" * 1000}]
        _cm(msgs, ctx_tokens=10000)
        update_real_prompt_usage(2000, None)
        assert get_context_usage_percent() == 20.0

    def test_update_real_prompt_usage_no_active_manager(self):
        set_active_context_manager(None)
        update_real_prompt_usage(100, "assistant")  # 不抛异常


# ═══════════════════════════════════════════════════════════════
# 3. MessageStatsCache 同步检测（同长度内容替换）
# ═══════════════════════════════════════════════════════════════

class TestCacheSyncDetection:

    def test_is_synced_after_resync(self):
        cache = MessageStatsCache()
        msgs = [{"role": "user", "content": "a"}]
        cache.resync(msgs)
        assert cache.is_synced(msgs)

    def test_detects_same_length_replacement(self):
        cache = MessageStatsCache()
        msgs = [{"role": "user", "content": "a"}, {"role": "user", "content": "bb"}]
        cache.resync(msgs)
        assert cache.is_synced(msgs)
        msgs[0] = {"role": "user", "content": "zzzz"}  # 同长度条数、新对象
        assert not cache.is_synced(msgs)

    def test_detects_length_change(self):
        cache = MessageStatsCache()
        msgs = [{"role": "user", "content": "a"}]
        cache.resync(msgs)
        msgs.append({"role": "user", "content": "b"})
        assert not cache.is_synced(msgs)

    def test_incremental_ops_keep_synced(self):
        cache = MessageStatsCache()
        msgs = [{"role": "user", "content": "a"}]
        cache.resync(msgs)
        m2 = {"role": "user", "content": "bb"}
        msgs.append(m2)
        cache.on_append(m2)
        assert cache.is_synced(msgs)
        m3 = {"role": "user", "content": "c"}
        msgs.insert(0, m3)
        cache.on_insert(0, m3)
        assert cache.is_synced(msgs)
        msgs[1] = {"role": "user", "content": "b2"}
        cache.on_replace(1, msgs[1])
        assert cache.is_synced(msgs)
        del msgs[0]
        cache.on_remove([0])
        assert cache.is_synced(msgs)

    def test_invalid_cache_not_synced(self):
        cache = MessageStatsCache()
        msgs = [{"role": "user", "content": "a"}]
        cache.resync(msgs)
        cache.invalidate()
        assert not cache.is_synced(msgs)

    def test_percentage_refreshes_after_replacement(self):
        """条数不变、消息内容替换 → 百分比自动重算（修复前滞留旧值）。"""
        msgs = [{"role": "user", "content": "a" * 1000}]  # 300 tok → 3.0%
        cm = _cm(msgs, ctx_tokens=10000)
        assert get_context_usage_percent() == 3.0
        msgs[0] = {"role": "user", "content": "a" * 2000}  # 600 tok → 6.0%
        cm.refresh_usage()
        assert get_context_usage_percent() == 6.0


# ═══════════════════════════════════════════════════════════════
# 4. 管线集成：usage chunk → 真实基线（主 Agent 生效 / SubAgent 跳过）
# ═══════════════════════════════════════════════════════════════

def _usage_chunk(prompt_tokens, completion_tokens=1):
    return {"choices": [], "usage": {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }}


async def _run_pipeline(label, chunks):
    from src.api.stream.pipeline_async import AsyncStreamPipeline
    from src.api.stream.context import StreamContext

    ctx = StreamContext("m", None, label, True)
    pipeline = AsyncStreamPipeline()

    async def gen():
        for ch in chunks:
            yield ch

    await pipeline.process(ctx, gen(), silent=True)
    return ctx


class TestPipelineBaselineIntegration:

    @pytest.mark.asyncio
    async def test_main_usage_sets_baseline(self):
        msgs = [{"role": "system", "content": "s" * 3000}]
        _cm(msgs, ctx_tokens=10000)
        await _run_pipeline("assistant", [_usage_chunk(1234)])
        # 真实基线校准（而非估算 9.0%）
        assert get_context_usage_percent() == 12.3

    @pytest.mark.asyncio
    async def test_subagent_usage_does_not_set_baseline(self):
        msgs = [{"role": "system", "content": "s" * 3000}]
        _cm(msgs, ctx_tokens=10000)
        await _run_pipeline("agent-1", [_usage_chunk(9000)])
        assert get_context_usage_percent() == 9.0  # 估算口径不变
