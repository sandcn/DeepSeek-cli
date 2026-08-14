"""/cost 缓存命中与输入输出计算测试。

覆盖链路：
1. 适配器 usage 解析（_extract_cache_usage / 流式 chunk / 非流式响应 / Anthropic）
2. 流式 Pipeline _handle_usage（原始格式 + 统一格式 usage）
3. 会话级统计累加（_Stats.accumulate_usage / reset_stats / 新导出函数）
4. 费用计算（compute_cost：命中优惠价 + 未命中全价 + 输出价 + 旧统计兼容）
5. /cost 展示（show_cost：命中/未命中分列 + 缓存优惠 + 旧格式兼容）
6. 配置（defaults 默认缓存价 / schema 清洗保留 input_cache_hit / _fill_default_cache_price 补默认价）
7. 非流式调用路径（_call_sync_async 透传缓存字段累加）
"""
from __future__ import annotations

import pytest

from src.api.adapters.base import (
    _extract_cache_usage,
    _parse_openai_stream_chunk,
)
from src.api.adapters.anthropic import AnthropicAdapter
from src.api.adapters.deepseek import DeepSeekAdapter
from src.api._stats_core import (
    _Stats,
    get_token_stats,
    get_total_input_cache_hit_tokens,
    get_total_input_cache_miss_tokens,
    reset_stats,
)
from src.api.stream.context import StreamContext
from src.api.stream.pipeline_async import AsyncStreamPipeline
from src.config.schema import _validate_rc
from src.config.defaults import PROVIDERS
from src.core.internal.commands._command_core import (
    compute_cost,
    _fill_default_cache_price,
    show_cost,
)
from src.core.adapters.output import DefaultOutputAdapter, reset_default_output_port


# ═══════════════════════════════════════════════════════════
# 1. 适配器 usage 解析
# ═══════════════════════════════════════════════════════════

class TestExtractCacheUsage:
    def test_deepseek_hit_miss_fields(self):
        """DeepSeek 原始格式：prompt_cache_hit_tokens / prompt_cache_miss_tokens。"""
        raw = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_cache_hit_tokens": 700,
            "prompt_cache_miss_tokens": 300,
        }
        assert _extract_cache_usage(raw) == (700, 300)

    def test_openai_prompt_tokens_details(self):
        """OpenAI 格式：prompt_tokens_details.cached_tokens，未命中 = 总输入 - 命中。"""
        raw = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 800},
        }
        assert _extract_cache_usage(raw) == (800, 200)

    def test_no_cache_fields(self):
        """无缓存字段时返回 (0, 0)。"""
        assert _extract_cache_usage({"prompt_tokens": 1000, "completion_tokens": 50}) == (0, 0)

    def test_non_dict(self):
        """非 dict / None 输入防御。"""
        assert _extract_cache_usage(None) == (0, 0)
        assert _extract_cache_usage("x") == (0, 0)

    def test_hit_only_no_miss(self):
        """仅命中字段时未命中按 0。"""
        raw = {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 1000}
        assert _extract_cache_usage(raw) == (1000, 0)


class TestParseOpenaiStreamChunk:
    def test_usage_includes_cache_fields(self):
        """流式 chunk usage 提取命中/未命中字段。"""
        chunk = {
            "choices": [{"delta": {"content": "hi"}}],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "prompt_cache_hit_tokens": 700,
                "prompt_cache_miss_tokens": 300,
            },
        }
        parsed = _parse_openai_stream_chunk(chunk)
        assert parsed["usage"] == {
            "input": 1000,
            "output": 50,
            "input_cache_hit": 700,
            "input_cache_miss": 300,
        }

    def test_no_usage(self):
        """无 usage 时保持 None。"""
        chunk = {"choices": [{"delta": {"content": "hi"}}]}
        assert _parse_openai_stream_chunk(chunk)["usage"] is None


class TestParseOpenaiCompatResponse:
    def test_usage_includes_cache_fields(self):
        """非流式响应 usage 提取命中/未命中字段。"""
        adapter = DeepSeekAdapter()
        response = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 100,
                "prompt_cache_hit_tokens": 1200,
                "prompt_cache_miss_tokens": 800,
            },
        }
        parsed = adapter._parse_openai_compat_response(response)
        assert parsed["usage"] == {
            "input": 2000,
            "output": 100,
            "input_cache_hit": 1200,
            "input_cache_miss": 800,
        }

    def test_preserve_raw_usage(self):
        """DeepSeek 路径保留 _raw 原始 usage。"""
        adapter = DeepSeekAdapter()
        response = {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 100,
                "prompt_cache_hit_tokens": 1200,
                "prompt_cache_miss_tokens": 800,
            },
        }
        parsed = adapter._parse_openai_compat_response(response, preserve_raw_usage=True)
        assert parsed["usage"]["_raw"]["prompt_cache_hit_tokens"] == 1200


class TestAnthropicCacheUsage:
    def test_parse_response_cache_fields(self):
        """Anthropic 响应：cache_read 映射命中，input_tokens 映射未命中。"""
        adapter = AnthropicAdapter()
        response = {
            "content": [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": 900,
                "output_tokens": 30,
                "cache_creation_input_tokens": 400,
                "cache_read_input_tokens": 100,
            },
        }
        parsed = adapter.parse_response(response)
        usage = parsed["usage"]
        assert usage["input"] == 1000          # 900 + 100
        assert usage["output"] == 30
        assert usage["input_cache_hit"] == 100   # cache_read
        assert usage["input_cache_miss"] == 900  # input_tokens（含 cache_creation）

    def test_parse_stream_chunk_message_delta(self):
        """Anthropic message_delta 事件提取缓存字段。"""
        adapter = AnthropicAdapter()
        chunk = {
            "type": "message_delta",
            "usage": {
                "input_tokens": 900,
                "output_tokens": 30,
                "cache_read_input_tokens": 100,
            },
        }
        parsed = adapter.parse_stream_chunk(chunk)
        assert parsed["usage"] == {
            "input": 1000,
            "output": 30,
            "input_cache_hit": 100,
            "input_cache_miss": 900,
        }


# ═══════════════════════════════════════════════════════════
# 2. 流式 Pipeline _handle_usage
# ═══════════════════════════════════════════════════════════

class TestPipelineHandleUsage:
    def _make_ctx(self):
        return StreamContext("deepseek-v4-pro", None, "test", silent=True)

    def test_raw_openai_format(self):
        """原始 OpenAI/DeepSeek usage chunk：缓存字段提取 + 累加。"""
        reset_stats()
        ctx = self._make_ctx()
        chunk = {
            "choices": [],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_cache_hit_tokens": 600,
                "prompt_cache_miss_tokens": 400,
            },
        }
        AsyncStreamPipeline()._handle_usage(ctx, chunk)
        assert ctx.usage["input"] == 1000
        assert ctx.usage["output"] == 200
        assert ctx.usage["input_cache_hit"] == 600
        assert ctx.usage["input_cache_miss"] == 400
        stats = get_token_stats()
        assert stats["input"] == 1000
        assert stats["input_cache_hit"] == 600
        assert stats["input_cache_miss"] == 400
        assert stats["calls"] == 1

    def test_unified_format(self):
        """统一格式 usage chunk（Anthropic 转换层产物）。"""
        reset_stats()
        ctx = self._make_ctx()
        chunk = {
            "choices": [],
            "usage": {"input": 500, "output": 80, "input_cache_hit": 200, "input_cache_miss": 300},
        }
        AsyncStreamPipeline()._handle_usage(ctx, chunk)
        stats = get_token_stats()
        assert stats["input"] == 500
        assert stats["input_cache_hit"] == 200
        assert stats["input_cache_miss"] == 300

    def test_estimated_output_correction(self):
        """output 用修正值累加（真实 - 估计），缓存字段用真实值。"""
        reset_stats()
        ctx = self._make_ctx()
        ctx.last_live_est = 150   # 流式估计已累计 150
        chunk = {
            "choices": [],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_cache_hit_tokens": 600,
                "prompt_cache_miss_tokens": 400,
            },
        }
        AsyncStreamPipeline()._handle_usage(ctx, chunk)
        stats = get_token_stats()
        assert stats["input"] == 1000
        assert stats["output"] == 50    # 200 - 150
        assert stats["input_cache_hit"] == 600
        assert stats["input_cache_miss"] == 400
        assert stats["calls"] == 1

    def test_accumulated_once(self):
        """usage_accumulated 置位后重复 chunk 不再累加。"""
        reset_stats()
        ctx = self._make_ctx()
        pipeline = AsyncStreamPipeline()
        chunk = {
            "choices": [],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_cache_hit_tokens": 500,
                "prompt_cache_miss_tokens": 500,
            },
        }
        pipeline._handle_usage(ctx, chunk)
        pipeline._handle_usage(ctx, chunk)   # 第二次应被跳过
        assert get_token_stats()["calls"] == 1
        assert get_token_stats()["input"] == 1000

    def test_context_usage_initialized_with_cache_fields(self):
        """StreamContext.usage 初始化含缓存字段。"""
        ctx = self._make_ctx()
        assert ctx.usage == {"input": 0, "output": 0, "input_cache_hit": 0, "input_cache_miss": 0}


# ═══════════════════════════════════════════════════════════
# 3. 会话级统计累加
# ═══════════════════════════════════════════════════════════

class TestStatsCore:
    def test_accumulate_usage_with_cache(self):
        """含缓存字段的 usage 正确累加。"""
        stats = _Stats()
        stats.accumulate_usage({"input": 1000, "output": 50, "input_cache_hit": 700, "input_cache_miss": 300})
        assert stats.token_stats["input"] == 1000
        assert stats.token_stats["output"] == 50
        assert stats.token_stats["input_cache_hit"] == 700
        assert stats.token_stats["input_cache_miss"] == 300
        assert stats.token_stats["calls"] == 1

    def test_accumulate_usage_without_cache_fields(self):
        """旧调用方（无缓存字段）兼容：按 0 累加。"""
        stats = _Stats()
        stats.accumulate_usage({"input": 100, "output": 10})
        assert stats.token_stats["input_cache_hit"] == 0
        assert stats.token_stats["input_cache_miss"] == 0

    def test_accumulate_usage_no_calls(self):
        """increment_calls=False 不增加调用次数（流式实时估计阶段）。"""
        stats = _Stats()
        stats.accumulate_usage({"input": 0, "output": 10}, increment_calls=False)
        assert stats.token_stats["calls"] == 0
        assert stats.token_stats["output"] == 10

    def test_reset_stats_structure(self):
        """reset_stats 重置含缓存字段的结构。"""
        reset_stats()
        from src.api.stats import accumulate_usage
        accumulate_usage({"input": 10, "output": 5, "input_cache_hit": 7, "input_cache_miss": 3})
        reset_stats()
        assert get_token_stats() == {
            "input": 0, "output": 0, "calls": 0,
            "input_cache_hit": 0, "input_cache_miss": 0,
        }

    def test_get_token_stats_snapshot_includes_cache(self):
        """模块级 get_token_stats 快照含缓存字段。"""
        reset_stats()
        from src.api.stats import accumulate_usage
        accumulate_usage({"input": 100, "output": 20, "input_cache_hit": 60, "input_cache_miss": 40})
        snap = get_token_stats()
        assert snap["input_cache_hit"] == 60
        assert snap["input_cache_miss"] == 40

    def test_new_exported_getters(self):
        """新导出的缓存 token 获取函数。"""
        reset_stats()
        from src.api.stats import accumulate_usage
        accumulate_usage({"input": 100, "output": 20, "input_cache_hit": 60, "input_cache_miss": 40})
        assert get_total_input_cache_hit_tokens() == 60
        assert get_total_input_cache_miss_tokens() == 40


# ═══════════════════════════════════════════════════════════
# 4. 费用计算 compute_cost
# ═══════════════════════════════════════════════════════════

_DEEPSEEK_PRICES = {"input": 0.55, "output": 2.19, "input_cache_hit": 0.07}


class TestComputeCost:
    def test_cache_hit_discounted_cost(self):
        """命中按优惠价、未命中按全价、输出按输出价。"""
        stats = {"input": 1500, "output": 80, "calls": 2, "input_cache_hit": 700, "input_cache_miss": 300}
        d = compute_cost(stats, _DEEPSEEK_PRICES)
        expected_input = (300 * 0.55 + 700 * 0.07) / 1_000_000
        expected_output = 80 * 2.19 / 1_000_000
        assert abs(d["total"] - (expected_input + expected_output)) < 1e-12
        assert d["cache_priced"] is True
        assert abs(d["saved"] - (700 * (0.55 - 0.07)) / 1_000_000) < 1e-12

    def test_legacy_stats_without_cache_fields(self):
        """旧统计（无缓存拆分）：全部输入按全价，费用与旧实现一致。"""
        stats = {"input": 1000, "output": 80, "calls": 1}
        d = compute_cost(stats, _DEEPSEEK_PRICES)
        assert d["input_hit"] == 0
        assert d["input_miss"] == 1000
        assert d["cache_priced"] is False
        expected = (1000 * 0.55 + 80 * 2.19) / 1_000_000
        assert abs(d["total"] - expected) < 1e-12
        assert d["saved"] == 0

    def test_missing_cache_hit_price_falls_back_to_input_price(self):
        """价格配置缺 input_cache_hit 时回退按全价（保守不低估）。"""
        stats = {"input": 1000, "output": 0, "calls": 1, "input_cache_hit": 1000, "input_cache_miss": 0}
        d = compute_cost(stats, {"input": 0.55, "output": 2.19})
        assert abs(d["total"] - 1000 * 0.55 / 1_000_000) < 1e-12
        assert d["saved"] == 0

    def test_negative_values_defensive(self):
        """负 token 数防御为 0。"""
        stats = {"input": 1000, "output": 0, "calls": 1, "input_cache_hit": -5, "input_cache_miss": -3}
        d = compute_cost(stats, _DEEPSEEK_PRICES)
        assert d["input_hit"] == 0
        assert d["input_miss"] == 0

    def test_hit_and_miss_sum(self):
        """hit + miss = input 的一致统计。"""
        stats = {"input": 12000, "output": 500, "calls": 1, "input_cache_hit": 9000, "input_cache_miss": 3000}
        d = compute_cost(stats, _DEEPSEEK_PRICES)
        assert d["input_hit"] + d["input_miss"] == d["total_input"]


# ═══════════════════════════════════════════════════════════
# 5. /cost 展示 show_cost
# ═══════════════════════════════════════════════════════════

class _FakeOutputPort(DefaultOutputAdapter):
    """捕获输出的端口替身。"""

    def __init__(self):
        super().__init__()
        self.lines = []

    def write(self, text: str, level: str = "info", source: str = "core") -> None:
        self.lines.append(text)


class _FakeCtx:
    def __init__(self, model="deepseek-v4-pro", config_port=None):
        self.state = {"model": model}
        self.config_port = config_port


@pytest.fixture(autouse=True)
def _reset_stats_before_each():
    reset_stats()
    yield
    reset_stats()
    reset_default_output_port()


class TestShowCost:
    def test_output_contains_cache_hit_details(self):
        """统计含缓存拆分时：缓存命中不计入输入行，单独展示并计算优惠。"""
        from src.api._stats_core import _stats
        _stats.token_stats = {
            "input": 12500, "output": 3200, "calls": 3,
            "input_cache_hit": 8000, "input_cache_miss": 4500,
        }
        port = _FakeOutputPort()
        DefaultOutputAdapter.set_default(port)
        show_cost(_FakeCtx())
        text = "\n".join(port.lines)
        assert "费用统计" in text
        # 输入行只含未命中（缓存命中的不算在输入里）
        assert "输入  4.5kt" in text
        assert "输入  12.5kt" not in text
        # 缓存命中单独一行
        assert "缓存  8.0kt (命中)" in text
        assert "输出  3.2kt" in text
        # 费用 = (8000*0.07 + 4500*0.55 + 3200*2.19)/1M = 0.010043 → $0.0100
        assert "费用  $0.0100" in text
        assert "命中优惠" in text

    def test_cache_hit_zero_still_shows_cache_line_when_priced(self):
        """统计含缓存字段但命中为 0 时，输入行显示全部未命中、缓存行显示 0。"""
        from src.api._stats_core import _stats
        _stats.token_stats = {
            "input": 1000, "output": 50, "calls": 1,
            "input_cache_hit": 0, "input_cache_miss": 1000,
        }
        port = _FakeOutputPort()
        DefaultOutputAdapter.set_default(port)
        show_cost(_FakeCtx())
        text = "\n".join(port.lines)
        assert "输入  1.0kt" in text
        assert "缓存  0t (命中)" in text
        # 费用全按未命中全价：1000*0.55 + 50*2.19 = 0.0006595 → $0.0007
        assert "费用  $0.0007" in text

    def test_legacy_stats_old_format(self):
        """旧统计（无缓存拆分）保持旧输出格式，全部输入视为未命中。"""
        from src.api._stats_core import _stats
        _stats.token_stats = {"input": 1000, "output": 200, "calls": 1}
        port = _FakeOutputPort()
        DefaultOutputAdapter.set_default(port)
        show_cost(_FakeCtx())
        text = "\n".join(port.lines)
        assert "输入  1.0kt" in text
        assert "命中" not in text
        assert "未命中" not in text
        assert "命中优惠" not in text
        # 全部按全价：1000*0.55 + 200*2.19 = 0.000988 → $0.0010
        assert "费用  $0.0010" in text

    def test_config_port_prices_used(self):
        """经 ConfigPort 获取价格（含 input_cache_hit）。"""
        from src.api._stats_core import _stats
        _stats.token_stats = {
            "input": 1000, "output": 0, "calls": 1,
            "input_cache_hit": 1000, "input_cache_miss": 0,
        }

        class FakeConfigPort:
            def get_token_prices(self):
                return {"deepseek-v4-pro": {"input": 0.55, "output": 2.19, "input_cache_hit": 0.07}}

        port = _FakeOutputPort()
        DefaultOutputAdapter.set_default(port)
        show_cost(_FakeCtx(config_port=FakeConfigPort()))
        text = "\n".join(port.lines)
        # 1000 * 0.07 / 1M = 0.00007 → $0.0001
        assert "费用  $0.0001" in text


# ═══════════════════════════════════════════════════════════
# 6. 配置：默认缓存价 / schema 清洗 / 默认价补充
# ═══════════════════════════════════════════════════════════

class TestConfigCachePrice:
    def test_providers_deepseek_default_includes_cache_hit(self):
        """DeepSeek 内置默认价格含 input_cache_hit。"""
        for model, prices in PROVIDERS["deepseek"]["token_prices"].items():
            assert prices["input_cache_hit"] > 0
            assert prices["input_cache_hit"] < prices["input"]

    def test_schema_preserves_input_cache_hit(self):
        """schema 清洗保留 input_cache_hit 字段。"""
        rc = {"token_prices": {"m1": {"input": "0.5", "output": "2.0", "input_cache_hit": "0.06"}}}
        out = _validate_rc(rc)
        assert out["token_prices"]["m1"] == {"input": 0.5, "output": 2.0, "input_cache_hit": 0.06}

    def test_schema_allows_missing_cache_hit(self):
        """schema 清洗兼容旧格式（无 input_cache_hit）。"""
        rc = {"token_prices": {"m1": {"input": "0.5", "output": "2.0"}}}
        out = _validate_rc(rc)
        assert out["token_prices"]["m1"] == {"input": 0.5, "output": 2.0}

    def test_fill_default_cache_price_missing(self):
        """价格缺失 input_cache_hit 时从内置 PROVIDERS 补充。"""
        prices = {"input": 0.55, "output": 2.19}
        filled = _fill_default_cache_price("deepseek-v4-pro", prices)
        assert filled["input_cache_hit"] == 0.07

    def test_fill_default_cache_price_keeps_existing(self):
        """已有 input_cache_hit 时保持原值。"""
        prices = {"input": 0.55, "output": 2.19, "input_cache_hit": 0.12}
        assert _fill_default_cache_price("deepseek-v4-pro", prices) is prices

    def test_fill_default_cache_price_no_match(self):
        """无匹配模型默认价时返回原样（compute_cost 回退全价）。"""
        prices = {"input": 0.55, "output": 2.19}
        assert _fill_default_cache_price("unknown-model", prices) == prices


# ═══════════════════════════════════════════════════════════
# 7. 非流式调用路径 _call_sync_async
# ═══════════════════════════════════════════════════════════

class TestSyncAsyncPath:
    async def test_call_sync_async_accumulates_cache(self, monkeypatch):
        """非流式调用：parse_response 的 usage 缓存字段透传累加到全局统计。"""
        reset_stats()

        class FakeAdapter:
            _protocol = ""
            def build_request_kwargs(self, **kwargs):
                return {"model": kwargs["model"], "messages": kwargs["messages"]}
            def parse_response(self, response):
                return {
                    "content": "ok",
                    "reasoning_content": "",
                    "usage": {
                        "input": 1000,
                        "output": 100,
                        "input_cache_hit": 700,
                        "input_cache_miss": 300,
                    },
                    "tool_calls": [],
                }

        async def fake_chat(**kwargs):
            return {}

        monkeypatch.setattr("src.api.model_async.get_adapter", lambda model: FakeAdapter())
        monkeypatch.setattr("src.api.model_async.chat_completions_async", fake_chat)

        from src.api.model_async import _call_sync_async
        await _call_sync_async([{"role": "user", "content": "hi"}], "deepseek-v4-pro", None)
        stats = get_token_stats()
        assert stats["input"] == 1000
        assert stats["output"] == 100
        assert stats["input_cache_hit"] == 700
        assert stats["input_cache_miss"] == 300
        assert stats["calls"] == 1
