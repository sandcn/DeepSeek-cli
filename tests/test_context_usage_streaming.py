"""上下文使用率实时刷新（流式增量）单元测试（2026-08-19 用户需求）。

需求：「上下文百分比要实时刷新」——AI 流式生成回复期间，模式行行首
``main · N%`` 应随输出内容增长实时上升（而非等回复结束消息追加才跳变）。

实现：
  - ``context_manager`` 模块级全局 ``_streaming_extra_tokens``（当前流式
    输出估算 tokens 增量）+ ``update_streaming_usage`` 入口（api 流式管线
    pipeline_async 每 ~0.1s 调用，流式结束 _cleanup_display 清零）；
  - ``StreamContext.streamed_output_tokens`` 单调累积（真实 usage 到达不
    清零——避免流式未结束时百分比短暂回落抖动）；
  - ``refresh_usage`` 统计口径叠加流式增量：百分比 =（系统提词 + 工具列表
    + 全部消息 + 流式增量）/ model_context_tokens；
  - SubAgent（label="agent-N"）流式跳过（其输出占 SubAgent 独立上下文，
    不占主 Agent 上下文）；
  - ``_tools_tokens`` 指纹缓存（_tools_tokens_cache + _tools_cache_fp）——
    流式期间 0.1s 刷新路径 O(1)，工具列表变化（set_tools / 原地增删）失效。

覆盖：
  - 全局流式增量 set/get（含异常值钳制）
  - refresh_usage 含流式增量（百分比随输出增长实时上升——核心需求）
  - update_streaming_usage 实时刷新全局百分比（主 Agent label="assistant"）
  - SubAgent label 跳过（不影响主 Agent 百分比）
  - 流式结束清零（百分比回落基线，不残留）
  - 清零后消息追加 refresh_usage 不双计（集成语义）
  - _tools_tokens 缓存复用与失效
  - pipeline_async.process 集成：流式过程中全局 pct 实时上升、结束后清零
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _clean_context_usage_globals():
    """每个测试后清理 context_manager 模块级全局（避免测试间污染）。

    清理项：全局百分比快照、流式增量 tokens、活跃实例引用——三者均为
    模块级单例，测试间残留会相互影响（如流式增量残留使后续测试基线虚高）。
    """
    yield
    from src.core.context_manager import (
        set_context_usage_percent, set_streaming_extra_tokens,
        set_active_context_manager,
    )
    set_context_usage_percent(None)
    set_streaming_extra_tokens(0)
    set_active_context_manager(None)


def _make_cm(msgs, tools=None, ctx_tokens: int = 10000):
    """构造 ContextManager（MockConfigAdapter 控制上下文窗口，测试精确）。"""
    from src.core.adapters.config import MockConfigAdapter
    from src.core.context_manager import ContextManager
    cfg = MockConfigAdapter({"model_context_tokens": ctx_tokens})
    return ContextManager(msgs, "m", tools=tools, config_port=cfg)


class TestStreamingExtraTokens:
    """模块级全局流式增量（set/get/钳制）。"""

    def test_set_get_roundtrip(self):
        from src.core.context_manager import (
            set_streaming_extra_tokens, get_streaming_extra_tokens,
        )
        set_streaming_extra_tokens(1500)
        assert get_streaming_extra_tokens() == 1500
        set_streaming_extra_tokens(0)
        assert get_streaming_extra_tokens() == 0

    def test_invalid_values_clamped(self):
        """负值/None/非数字字符串归零；数值字符串可转 int。"""
        from src.core.context_manager import (
            set_streaming_extra_tokens, get_streaming_extra_tokens,
        )
        set_streaming_extra_tokens(-5)
        assert get_streaming_extra_tokens() == 0
        set_streaming_extra_tokens(None)
        assert get_streaming_extra_tokens() == 0
        set_streaming_extra_tokens("abc")  # 不可解析 → 归零不抛异常
        assert get_streaming_extra_tokens() == 0
        set_streaming_extra_tokens("100")
        assert get_streaming_extra_tokens() == 100

    def test_default_zero(self):
        """默认（未写入）为 0——无流式时不虚增。"""
        from src.core.context_manager import get_streaming_extra_tokens
        assert get_streaming_extra_tokens() == 0


class TestRefreshUsageIncludesStreaming:
    """refresh_usage 统计口径含流式增量（核心需求：百分比实时上升）。"""

    def test_percent_rises_with_streaming_delta(self):
        """流式增量写入 → refresh_usage → 全局 pct 实时上升。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            set_streaming_extra_tokens,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        set_streaming_extra_tokens(0)
        msgs = [{"role": "system", "content": "s" * 1000}]
        cm = _make_cm(msgs, ctx_tokens=10000)
        base_tokens = estimate_tokens("s" * 1000)
        base = get_context_usage_percent()
        assert base == round(base_tokens / 10000 * 100, 1)
        # 模拟流式输出累积 1500 tok
        set_streaming_extra_tokens(1500)
        cm.refresh_usage()
        p1 = get_context_usage_percent()
        assert p1 > base
        assert p1 == round((base_tokens + 1500) / 10000 * 100, 1)
        # 输出继续增长 3000 tok → 百分比继续上升（实时性）
        set_streaming_extra_tokens(3000)
        cm.refresh_usage()
        p2 = get_context_usage_percent()
        assert p2 > p1
        assert p2 == round((base_tokens + 3000) / 10000 * 100, 1)

    def test_streaming_delta_includes_tools_base(self):
        """流式增量叠加在（系统提词 + 工具列表）基线之上。"""
        import json as _json
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            set_streaming_extra_tokens,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        set_streaming_extra_tokens(0)
        msgs = [{"role": "system", "content": "s" * 1000}]
        tools = [{"type": "function", "function": {"name": "bash"}}]
        cm = _make_cm(msgs, tools=tools, ctx_tokens=10000)
        base_tokens = (
            estimate_tokens("s" * 1000)
            + estimate_tokens(_json.dumps(tools[0], ensure_ascii=False))
        )
        assert get_context_usage_percent() == round(base_tokens / 10000 * 100, 1)
        set_streaming_extra_tokens(500)
        cm.refresh_usage()
        assert get_context_usage_percent() == round((base_tokens + 500) / 10000 * 100, 1)


class TestUpdateStreamingUsage:
    """update_streaming_usage 实时刷新入口（api 流式管线调用）。"""

    @staticmethod
    def _active_cm(ctx_tokens: int = 10000):
        msgs = [{"role": "system", "content": "s" * 1000}]
        return _make_cm(msgs, ctx_tokens=ctx_tokens)

    def test_updates_percent_live(self):
        """主 Agent 流式（label="assistant"）→ 全局 pct 实时刷新。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage,
        )
        set_context_usage_percent(None)
        self._active_cm()
        base = get_context_usage_percent()
        update_streaming_usage(1500, "assistant")
        p1 = get_context_usage_percent()
        assert p1 > base
        update_streaming_usage(3000, "assistant")
        p2 = get_context_usage_percent()
        assert p2 > p1

    def test_label_none_counts_as_main(self):
        """label 为 None（非 TUI/缺省路径）同样计入主 Agent。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage,
        )
        set_context_usage_percent(None)
        self._active_cm()
        base = get_context_usage_percent()
        update_streaming_usage(1000, None)
        assert get_context_usage_percent() > base

    def test_subagent_label_skipped(self):
        """SubAgent 流式（label="agent-N"）不更新主 Agent 全局 pct。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage, get_streaming_extra_tokens,
        )
        set_context_usage_percent(None)
        self._active_cm()
        base = get_context_usage_percent()
        update_streaming_usage(5000, "agent-1")
        assert get_context_usage_percent() == base
        assert get_streaming_extra_tokens() == 0  # 全局增量未被污染

    def test_background_subagent_label_skipped(self):
        """后台 SubAgent 流式（label="sa-xxx" task_id）同样不更新主 Agent pct。

        ★ 2026-08-20 修复：后台 subagent（subagent 工具直接后台派发）label 为
        task_id（"sa-xxx"）而非 "agent-" 前缀——修复前其流式增量被写入全局
        并触发主 Agent refresh_usage()，主 Agent 上下文百分比被 subagent
        动态信息污染（虚高）。
        """
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage, get_streaming_extra_tokens,
        )
        set_context_usage_percent(None)
        self._active_cm()
        base = get_context_usage_percent()
        update_streaming_usage(5000, "sa-abc123def456")
        assert get_context_usage_percent() == base
        assert get_streaming_extra_tokens() == 0  # 全局增量未被污染

    def test_background_subagent_clear_does_not_touch_main_delta(self):
        """后台 SubAgent 流式结束清零（update_streaming_usage(0, "sa-xxx")）
        不得清除主 Agent 的流式增量（流式增量互不干扰）。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage, get_streaming_extra_tokens,
        )
        set_context_usage_percent(None)
        self._active_cm()
        base = get_context_usage_percent()
        # 主 Agent 流式进行中：增量 2000
        update_streaming_usage(2000, "assistant")
        assert get_context_usage_percent() > base
        # 后台 SubAgent 流式期间写入 + 结束清零（修复前会覆盖/清零主 Agent 增量）
        update_streaming_usage(1500, "sa-abc123def456")
        update_streaming_usage(0, "sa-abc123def456")
        assert get_context_usage_percent() > base   # 主 Agent 增量保持
        assert get_streaming_extra_tokens() == 2000  # 未被覆盖/清零

    def test_is_subagent_stream_label(self):
        """_is_subagent_stream_label 辅助函数：前台/后台 SubAgent 均识别。"""
        from src.core.context_manager import _is_subagent_stream_label
        assert _is_subagent_stream_label("agent-1") is True
        assert _is_subagent_stream_label("agent-10") is True
        assert _is_subagent_stream_label("sa-abc123def456") is True
        assert _is_subagent_stream_label("assistant") is False
        assert _is_subagent_stream_label(None) is False
        assert _is_subagent_stream_label("") is False

    def test_clear_after_stream(self):
        """流式结束清零（update_streaming_usage(0)）→ 百分比回落基线。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage,
        )
        set_context_usage_percent(None)
        self._active_cm()
        base = get_context_usage_percent()
        update_streaming_usage(2000, "assistant")
        assert get_context_usage_percent() > base
        update_streaming_usage(0, "assistant")  # _cleanup_display 清零语义
        assert get_context_usage_percent() == base

    def test_no_active_cm_no_crash(self):
        """无活跃 ContextManager（纯 api 场景）调用不崩溃。"""
        from src.core.context_manager import (
            set_active_context_manager, update_streaming_usage,
            set_streaming_extra_tokens, get_streaming_extra_tokens,
        )
        set_active_context_manager(None)
        update_streaming_usage(1500, "assistant")
        assert get_streaming_extra_tokens() == 1500
        update_streaming_usage(0, "agent-1")  # SubAgent 跳过不清理
        update_streaming_usage(0, None)       # 显式清零（自包含）
        assert get_streaming_extra_tokens() == 0

    def test_no_double_count_after_message_append(self):
        """流式结束清零后消息追加 refresh_usage 不双计（集成语义）。"""
        from src.core.adapters.config import MockConfigAdapter
        from src.core.context_manager import (
            ContextManager, set_context_usage_percent, get_context_usage_percent,
            update_streaming_usage,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        msgs = [{"role": "system", "content": "s" * 1000}]
        cfg = MockConfigAdapter({"model_context_tokens": 10000})
        cm = ContextManager(msgs, "m", config_port=cfg)
        base = get_context_usage_percent()
        # 流式：增量 2000 tok
        update_streaming_usage(2000, "assistant")
        assert get_context_usage_percent() > base
        # 流式结束：清零（_cleanup_display 语义）
        update_streaming_usage(0, "assistant")
        # 消息追加（_append_assistant_message → refresh_usage）
        msgs.append({"role": "assistant", "content": "a" * 3000})
        cm.refresh_usage()
        expected = round(
            (estimate_tokens("s" * 1000) + estimate_tokens("a" * 3000))
            / 10000 * 100, 1,
        )
        assert get_context_usage_percent() == expected


class TestToolsTokensCache:
    """_tools_tokens 指纹缓存（流式 0.1s 刷新路径 O(1)）。"""

    @staticmethod
    def _tools():
        return [
            {"type": "function", "function": {
                "name": "read_file", "description": "读文件",
                "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {
                "name": "bash", "description": "执行命令",
                "parameters": {"type": "object", "properties": {}}}},
        ]

    def test_cache_reused(self):
        """重复调用复用缓存（结果一致 + 缓存字段已写入）。"""
        cm = _make_cm([], tools=self._tools())
        t1 = cm._tools_tokens()
        t2 = cm._tools_tokens()
        assert t1 == t2
        assert cm._tools_tokens_cache is not None
        assert cm._tools_tokens_cache == t1

    def test_set_tools_invalidates_cache(self):
        """set_tools 工具变化 → 缓存失效重算（百分比随工具变化更新）。"""
        cm = _make_cm([])
        assert cm._tools_tokens() == 0
        cm.set_tools(self._tools())
        assert cm._tools_tokens() > 0          # 缓存失效后按新工具重算
        first = cm._tools_tokens()
        cm.set_tools([])
        assert cm._tools_tokens() == 0         # 再次失效 → 0
        assert first > 0

    def test_inplace_append_invalidates_cache(self):
        """原地增删 self.tools（不经 set_tools）→ 指纹变化触发缓存失效。"""
        tools = self._tools()
        cm = _make_cm([], tools=tools)
        t1 = cm._tools_tokens()
        fp1 = cm._tools_cache_fp
        cm.tools.append({"type": "function", "function": {
            "name": "ls", "description": "列出目录内容" + "x" * 500}})
        t2 = cm._tools_tokens()
        assert cm._tools_cache_fp != fp1      # 指纹变化 → 缓存已失效重算
        assert t2 > t1                        # 新工具计入 → 增长
        cm.tools.pop()
        t3 = cm._tools_tokens()
        assert t3 == t1                       # 移除后回落


class TestPipelineIntegration:
    """pipeline_async.process 集成（真实链路：流式增量生命周期）。"""

    async def _run_stream(self, ctx, chunks):
        from src.api.stream.pipeline_async import AsyncStreamPipeline
        pipeline = AsyncStreamPipeline()

        async def gen():
            for ch in chunks:
                yield ch
                await asyncio.sleep(0.02)

        task = asyncio.create_task(pipeline.process(ctx, gen(), silent=True))
        return task

    async def test_percent_rises_during_stream_and_clears_after(self):
        """process 流式中全局 pct 实时上升；结束后清零回落基线。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            get_streaming_extra_tokens,
        )
        from src.api.stream.context import StreamContext
        set_context_usage_percent(None)
        _make_cm([{"role": "system", "content": "s" * 1000}], ctx_tokens=10000)
        base = get_context_usage_percent()
        ctx = StreamContext("test-model", None, "assistant", True)
        chunks = [{"choices": [{"delta": {"content": "a" * 100}}]} for _ in range(10)]
        task = await self._run_stream(ctx, chunks)

        # 流式过程中：全局 pct 实时上升（> 基线）
        seen_rise = False
        while not task.done():
            await asyncio.sleep(0.01)
            p = get_context_usage_percent()
            if p is not None and p > base:
                seen_rise = True
                break
        await task
        assert seen_rise, "流式过程中全局 pct 应实时上升（> 基线）"
        # 流式结束：增量清零 + 百分比回落基线
        assert get_streaming_extra_tokens() == 0
        assert get_context_usage_percent() == base
        # 内容已累积
        assert ctx.content_full

    async def test_subagent_stream_does_not_touch_main_percent(self):
        """SubAgent 流式（label="agent-1"）process 不更新主 Agent 全局 pct。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            get_streaming_extra_tokens,
        )
        from src.api.stream.context import StreamContext
        set_context_usage_percent(None)
        _make_cm([{"role": "system", "content": "s" * 1000}], ctx_tokens=10000)
        base = get_context_usage_percent()
        ctx = StreamContext("test-model", None, "agent-1", True)
        chunks = [{"choices": [{"delta": {"content": "a" * 100}}]} for _ in range(5)]
        task = await self._run_stream(ctx, chunks)
        await asyncio.sleep(0.3)   # 覆盖 0.1s 节流窗口（若错误计入应已触发）
        p = get_context_usage_percent()
        assert p == base           # SubAgent 流不影响主 Agent 百分比
        assert get_streaming_extra_tokens() == 0
        await task

    async def test_background_subagent_stream_does_not_touch_main_percent(self):
        """后台 SubAgent 流式（label="sa-xxx" task_id）process 不更新主 Agent pct。

        ★ 2026-08-20 修复：后台 subagent label 为 task_id（"sa-xxx"）而非
        "agent-" 前缀——修复前 pipeline 每 ~0.1s 调 update_streaming_usage
        时该 label 不被识别为 SubAgent，流式增量污染主 Agent 全局百分比。
        """
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
            get_streaming_extra_tokens,
        )
        from src.api.stream.context import StreamContext
        set_context_usage_percent(None)
        _make_cm([{"role": "system", "content": "s" * 1000}], ctx_tokens=10000)
        base = get_context_usage_percent()
        ctx = StreamContext("test-model", None, "sa-abc123def456", True)
        chunks = [{"choices": [{"delta": {"content": "a" * 100}}]} for _ in range(5)]
        task = await self._run_stream(ctx, chunks)
        await asyncio.sleep(0.3)   # 覆盖 0.1s 节流窗口（若错误计入应已触发）
        p = get_context_usage_percent()
        assert p == base           # 后台 SubAgent 流不影响主 Agent 百分比
        assert get_streaming_extra_tokens() == 0
        await task

    async def test_streamed_output_tokens_monotonic(self):
        """streamed_output_tokens 单调累积（真实 usage 到达不清零）。"""
        from src.api.stream.context import StreamContext
        ctx = StreamContext("test-model", None, "assistant", True)
        chunks = [
            {"choices": [{"delta": {"content": "a" * 500}}]},
            # 模拟真实 usage chunk（token_estimate 被清零）
            {"choices": [{"delta": {"content": "b" * 500}}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 50}},
            {"choices": [{"delta": {"content": "c" * 500}}]},
        ]
        task = await self._run_stream(ctx, chunks)
        await task
        # usage chunk 后仍有 content 输出 → streamed_output_tokens 保持单调
        assert ctx.streamed_output_tokens > 0
