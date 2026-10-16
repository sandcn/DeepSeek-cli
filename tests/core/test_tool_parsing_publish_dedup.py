"""``tool_parsing`` 事件发布去重回归测试（2026-09-20 解析进度行卡住修复）。

修复背景（同一报障的命令洪流源头）：``ToolCallsHandler`` 对**每个**
tool_calls 增量 chunk 都发布一次 ``tool_parsing``（只要参数预览非空），而主
agent 侧 ``EventDispatcher._on_tool_parsing`` 每次都映射为一条 CRITICAL
（prio0）的 ``MainPhaseCmd(phase="parsing")``——单次工具解析实测产生上千条
**完全幂等**的重复命令（``_do_main_phase`` 对同 phase 不重置计时、不重开
通道，重复推送零信息增量）。这些 prio0 命令挤满渲染队列批处理配额
（``max_batch_size``），把低优先级的解析进度行命令（``ParseInfoCmd``）饿死
→ 进度行 token/耗时数字长时间不刷新。

修复：参数预览（``_args_preview``，上限 200 字符）**值未变化**时不再重复
发布 ``tool_parsing``——预览一旦达到上限即恒定，事件数从 O(分片数) 降为
O(预览变化次数)；subagent 面板依赖的预览详情在值不变时无需刷新（行为等价）。

本测试覆盖：首次发布语义保留、预览稳定后不再重复发布、预览变化时仍发布、
单次大参数流的事件数远小于 chunk 数。
"""

from __future__ import annotations

import asyncio

import pytest

from src.api.stream.context import StreamContext
from src.api.stream.handlers.tool_calls import ToolCallsHandler


class _RecordingDisplay:
    """记录 ``tool_parsing`` 调用的显示桩。"""

    def __init__(self):
        self.calls: list = []

    def tool_parsing(self, label, tool_name, arguments="", tool_id=""):
        self.calls.append((label, tool_name, arguments, tool_id))

    def update_parse_info(self, *a, **k):  # pragma: no cover - 未被本测试调用
        pass

    def parse_info_done(self, *a, **k):  # pragma: no cover
        pass


def _feed(chunks: int, chunk_size: int = 40):
    """按流式分片喂入一个工具调用，返回 (display, ctx)。"""
    display = _RecordingDisplay()
    ctx = StreamContext("m", display, "main", False)
    handler = ToolCallsHandler()
    payload = "".join("abcdefghij" for _ in range(1 + (chunks * chunk_size) // 10))

    async def _run():
        for i in range(chunks):
            part = payload[i * chunk_size:(i + 1) * chunk_size]
            fn = {"name": "write_file"} if i == 0 else {}
            delta = [{
                "index": 0,
                "id": "call_1" if i == 0 else None,
                "function": {**fn, "arguments": part},
            }]
            await handler.handle(ctx, delta)

    asyncio.run(_run())
    return display, ctx


class TestToolParsingDedup:
    """预览未变化时不重复发布 ``tool_parsing``。"""

    def test_first_publish_kept(self):
        """首次检测到工具调用仍发布一次（arguments 为空，进入解析阶段信号）。"""
        display, _ctx = _feed(chunks=1)
        assert len(display.calls) == 1
        assert display.calls[0][1] == "write_file"
        assert display.calls[0][2] == ""
        assert display.calls[0][3] == "call_1"

    def test_event_count_far_below_chunk_count(self):
        """单次大参数流：事件数远小于分片数（洪流被消除）。"""
        display, _ctx = _feed(chunks=60, chunk_size=40)
        assert len(display.calls) < 15, f"仍按 chunk 发布: {len(display.calls)}"
        assert len(display.calls) < 60

    def test_no_republish_after_preview_saturated(self):
        """预览达到 200 字符上限后不再发布新事件（恒定值去重）。"""
        display, _ctx = _feed(chunks=30, chunk_size=40)
        after_saturation = len(display.calls)
        display2, _ctx2 = _feed(chunks=120, chunk_size=40)
        # chunk 数翻 4 倍，事件数不应随分片线性增长
        assert len(display2.calls) <= after_saturation + 1

    def test_publish_when_preview_changes(self):
        """预览值变化时仍发布（subagent 面板详情可继续更新）。"""
        display, ctx = _feed(chunks=3, chunk_size=10)
        previews = [c[2] for c in display.calls]
        # 首事件为空（首次解析信号），其后为逐次变化的预览值
        assert previews[0] == ""
        assert len(set(previews)) == len(previews)
        assert ctx.tool_calls_map[0].get("_published_preview")

    def test_published_preview_recorded_only_on_success(self):
        """发布成功后记录 ``_published_preview``（失败不记录，保证重试发布）。"""

        class _Failing(_RecordingDisplay):
            def tool_parsing(self, *a, **k):
                raise RuntimeError("boom")

        display = _Failing()
        ctx = StreamContext("m", display, "main", False)
        handler = ToolCallsHandler()

        async def _run():
            for i in range(3):
                fn = {"name": "write_file"} if i == 0 else {}
                await handler.handle(ctx, [{
                    "index": 0,
                    "id": "call_1" if i == 0 else None,
                    "function": {**fn, "arguments": "x" * 20},
                }])

        asyncio.run(_run())
        assert "_published_preview" not in ctx.tool_calls_map[0]

    def test_no_display_is_safe(self):
        """无 display（静默路径）时不发布也不报错。"""
        ctx = StreamContext("m", None, "main", True)
        handler = ToolCallsHandler()

        async def _run():
            await handler.handle(ctx, [{
                "index": 0, "id": "c1",
                "function": {"name": "write_file", "arguments": "{}"},
            }])

        asyncio.run(_run())
        assert ctx.tool_calls_map[0]["name"] == "write_file"
