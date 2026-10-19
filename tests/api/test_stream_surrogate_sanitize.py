"""流式管线：孤立 UTF-16 代理项清洗回归测试。

背景：模型服务端可能把 emoji 的 UTF-16 代理对拆到两个 SSE 帧（两帧 JSON 里
分别是 ``\\ud83d`` / ``\\ude00`` 转义），解析后每帧都是孤立代理项——该码点
无法以 UTF-8 编码：渲染写终端、会话自动保存、下一次请求序列化都会抛
``UnicodeEncodeError: surrogates not allowed``。

覆盖：
  - ``StreamContext.prepare_display_delta``：跨增量代理对挂起/还原、孤立代理项
    → U+FFFD、通道隔离、流结束收尾；
  - ``ContentHandler`` / ``ReasoningHandler``：发布的渲染增量已清洗，累积全文
    保留原文（结果构建时统一清洗）；
  - ``AsyncStreamPipeline._build_result``：content / reasoning / 工具参数清洗；
  - ``AsyncStreamPipeline._cleanup_display``：悬挂高代理项以 U+FFFD 收尾且早于
    PhaseDoneEvent 发布。
"""

from __future__ import annotations

import asyncio

from src._unicode_text import REPLACEMENT_CHARACTER
from src.api.stream.context import StreamContext
from src.api.stream.handlers.content import ContentHandler
from src.api.stream.handlers.reasoning import ReasoningHandler
from src.api.stream.pipeline_async import AsyncStreamPipeline

LONE_HIGH = "\ud83d"   # 😀 的 UTF-16 高代理项
LONE_LOW = "\ude00"    # 😀 的 UTF-16 低代理项
EMOJI = "\U0001F600"


# ═══════════════════════════════════════════════════════════
# 1. StreamContext：按增量的渲染文本清洗
# ═══════════════════════════════════════════════════════════

class TestPrepareDisplayDelta:
    def _ctx(self):
        return StreamContext("m", None, "assistant", True)

    def test_normal_text_passthrough(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta("普通内容") == "普通内容"
        assert ctx.prepare_display_delta(EMOJI) == EMOJI

    def test_empty_delta(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta("") == ""

    def test_split_pair_across_deltas_is_restored(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta(LONE_HIGH) == ""  # 高代理项先挂起
        assert ctx.prepare_display_delta(LONE_LOW) == EMOJI  # 与下一帧拼接还原

    def test_split_pair_with_surrounding_text(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta(f"完成{LONE_HIGH}") == "完成"
        assert ctx.prepare_display_delta(f"{LONE_LOW}了") == f"{EMOJI}了"

    def test_lone_pair_in_single_delta_is_restored(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta(LONE_HIGH + LONE_LOW) == EMOJI

    def test_lone_high_not_followed_by_low_becomes_replacement(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta(LONE_HIGH) == ""
        assert ctx.prepare_display_delta("文本") == f"{REPLACEMENT_CHARACTER}文本"

    def test_lone_low_becomes_replacement(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta(f"a{LONE_LOW}") == (
            f"a{REPLACEMENT_CHARACTER}"
        )

    def test_channels_are_independent(self):
        """content 挂起的高代理项不得与 reasoning 的低代理项错配。"""
        ctx = self._ctx()
        assert ctx.prepare_display_delta(LONE_HIGH, "content") == ""
        assert ctx.prepare_display_delta(LONE_LOW, "reasoning") == (
            REPLACEMENT_CHARACTER
        )
        # content 通道的悬挂项仍在，后续低代理项可还原
        assert ctx.prepare_display_delta(LONE_LOW, "content") == EMOJI

    def test_flush_pending_surrogate(self):
        ctx = self._ctx()
        assert ctx.prepare_display_delta(LONE_HIGH) == ""
        assert ctx.flush_display_surrogate("content") == REPLACEMENT_CHARACTER
        assert ctx.flush_display_surrogate("content") == ""  # 已消费


# ═══════════════════════════════════════════════════════════
# 2. Handler：发布事件文本已清洗，累积全文保留原文
# ═══════════════════════════════════════════════════════════

class _EventRecorder:
    def __init__(self):
        self.events: list = []

    def __call__(self, event_type, **kwargs):
        self.events.append((event_type, kwargs))

    def texts(self, event_type: str) -> list[str]:
        return [kw.get("text", "") for name, kw in self.events if name == event_type]


class TestHandlerDeltaSanitize:
    def test_content_handler_publishes_clean_text(self, monkeypatch):
        rec = _patch_publishers(monkeypatch)
        ctx = StreamContext("m", None, "assistant", True)
        handler = ContentHandler()

        handler.handle(ctx, LONE_HIGH)
        handler.handle(ctx, LONE_LOW)
        handler.flush(ctx.label)

        published = "".join(rec.texts("ContentChunkEvent"))
        assert LONE_HIGH not in published
        assert LONE_LOW not in published
        assert published == EMOJI
        # 累积全文保留原文（拼接后相邻，_build_result 统一清洗为完整 emoji）
        assert ctx.content_full == LONE_HIGH + LONE_LOW

    def test_reasoning_handler_publishes_clean_text(self, monkeypatch):
        rec = _patch_publishers(monkeypatch)
        ctx = StreamContext("m", None, "assistant", True)
        handler = ReasoningHandler()

        # 高代理项在增量末尾 → 挂起（不立即渲染）；下一增量带来低代理项后
        # 与 "想" 之前的增量拼接，还原为完整 emoji
        handler.handle(ctx, f"想{LONE_HIGH}")
        handler.flush(ctx.label)
        assert "".join(rec.texts("ReasoningChunkEvent")) == "想"

        handler.handle(ctx, f"{LONE_LOW}完成")
        handler.flush(ctx.label)
        assert "".join(rec.texts("ReasoningChunkEvent")) == f"想{EMOJI}完成"
        assert ctx.reasoning_full == f"想{LONE_HIGH}{LONE_LOW}完成"


# ═══════════════════════════════════════════════════════════
# 3. _build_result：最终结果清洗
# ═══════════════════════════════════════════════════════════

class TestBuildResultSanitize:
    def _ctx(self):
        return StreamContext("m", None, "assistant", True)

    def test_content_split_pair_restored(self):
        ctx = self._ctx()
        ctx.content_full = f"前{LONE_HIGH}{LONE_LOW}后"
        _reasoning, content, _usage, _tool_calls = AsyncStreamPipeline()._build_result(ctx)
        assert content == f"前{EMOJI}后"
        assert content.encode("utf-8")

    def test_reasoning_lone_surrogate_replaced(self):
        ctx = self._ctx()
        ctx.reasoning_full = f"思考{LONE_HIGH}"
        ctx.content_full = "回答"
        reasoning, content, _usage, _tool_calls = AsyncStreamPipeline()._build_result(ctx)
        assert reasoning == f"思考{REPLACEMENT_CHARACTER}"
        assert content == "回答"

    def test_interrupted_path_sanitized(self):
        ctx = self._ctx()
        ctx.content_full = f"半截{LONE_HIGH}"
        ctx.esc_interrupted = True
        _reasoning, content, _usage, tool_calls = AsyncStreamPipeline()._build_result(ctx)
        assert LONE_HIGH not in content
        assert REPLACEMENT_CHARACTER in content
        assert tool_calls == []

    def test_tool_call_arguments_sanitized(self):
        ctx = self._ctx()
        ctx.tool_calls_map = {
            0: {
                "id": "call_1",
                "name": "bash",
                "arguments": f'{{"cmd": "echo {LONE_HIGH}"}}',
            },
        }
        _reasoning, _content, _usage, tool_calls = AsyncStreamPipeline()._build_result(ctx)
        assert len(tool_calls) == 1
        args = tool_calls[0]["arguments"]
        assert LONE_HIGH not in repr(args)
        assert REPLACEMENT_CHARACTER in args["cmd"]


# ═══════════════════════════════════════════════════════════
# 4. _cleanup_display：悬挂高代理项收尾
# ═══════════════════════════════════════════════════════════

def _patch_publishers(monkeypatch) -> _EventRecorder:
    """替换流式模块绑定的 ``publish_event``（同 tests/tui/test_segment_end_flush）。"""
    import src.api.stream.context as ctx_mod
    import src.api.stream.handlers._base as handlers_base
    import src.api.stream.pipeline_async as phase_mod

    rec = _EventRecorder()
    monkeypatch.setattr(phase_mod, "publish_event", rec)
    monkeypatch.setattr(ctx_mod, "publish_event", rec)
    monkeypatch.setattr(handlers_base, "publish_event", rec)
    return rec


class TestCleanupDisplaySurrogate:
    def test_pending_high_surrogate_flushed_before_phase_done(self, monkeypatch):
        rec = _patch_publishers(monkeypatch)
        ctx = StreamContext("m", None, "assistant", True)
        ctx.content_full = "回答"
        # 模拟渲染侧已挂起一个高代理项（低代理项始终未到）
        assert ctx.prepare_display_delta(LONE_HIGH) == ""

        asyncio.run(AsyncStreamPipeline()._cleanup_display(ctx))

        names = [name for name, _kw in rec.events]
        content_texts = rec.texts("ContentChunkEvent")
        assert content_texts == [REPLACEMENT_CHARACTER]
        assert names.index("ContentChunkEvent") < names.index("PhaseDoneEvent")

    def test_no_pending_no_extra_event(self, monkeypatch):
        rec = _patch_publishers(monkeypatch)
        ctx = StreamContext("m", None, "assistant", True)
        ctx.content_full = "回答"
        ctx.prepare_display_delta("正常内容")

        asyncio.run(AsyncStreamPipeline()._cleanup_display(ctx))

        assert rec.texts("ContentChunkEvent") == []
