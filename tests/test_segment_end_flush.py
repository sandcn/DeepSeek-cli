"""「思考和回答完成 → 发事件收尾流式 markdown」回归测试。

需求（主 Agent 消息区）：
  思考和回答都完成时要发一个事件，让流式 markdown 渲染出**所有**剩余内容
  并**清空 status**（未闭合块末尾的流式指示 spinner / 角色头 spinner 帧）。

实现：
  - ``pipeline_async._cleanup_display`` 在正常结束（未中断、工具参数未中断）
    且本流有产出（reasoning/content/tool_calls 任一）时发布
    ``PhaseDoneEvent(phase="segment_end")``——不再只在「有工具调用」分支发送，
    纯思考/仅回答的正常收尾同样发事件；
  - ``apply._do_phase_done`` 处理 ``segment_end`` → ``AppModel.finish_stream_render()``
    ——关闭推理/内容两个流式通道（``renderer.close()`` 刷出解析器残差 = 渲染出
    所有），块标记 ``closed`` 后流式指示 spinner 自动回退静态（清空 status）。

本测试覆盖：完成事件发布（含无工具/中断路径）、收尾渲染出所有、流式指示清空、
幂等性。
"""

from __future__ import annotations

import asyncio

from src.api.stream.context import StreamContext
from src.api.stream.pipeline_async import AsyncStreamPipeline
from src.tui._const import ContentCmd, PhaseDoneCmd, ReasoningCmd
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel


# ═══════════════════════════════════════════════════════════
# 1. pipeline：完成事件（segment_end）发布
# ═══════════════════════════════════════════════════════════

class _EventRecorder:
    """记录 ``publish_event`` 调用（event_type + kwargs）。"""

    def __init__(self):
        self.events: list = []
        self.phase_done_phases: list = []

    def __call__(self, event_type, **kwargs):
        self.events.append((event_type, kwargs))
        if event_type == "PhaseDoneEvent":
            self.phase_done_phases.append(kwargs.get("phase"))


def _patch_publishers(monkeypatch, rec):
    """替换所有流式模块已绑定的 ``publish_event``（记录全量事件）。

    ``context.py`` / ``pipeline_async.py`` / ``handlers/_base.py`` 各自
    ``from ...events import publish_event`` 绑定模块属性——逐一替换才可捕获
    全部事件（PhaseDone 由 ctx.publish_phase_done_once 经 context 模块发布，
    内容 chunk 由 handlers._base 发布，segment_end 由 pipeline_async 发布）。
    """
    import src.api.stream.pipeline_async as pa
    import src.api.stream.context as pc
    import src.api.stream.handlers._base as phb

    monkeypatch.setattr(pa, "publish_event", rec)
    monkeypatch.setattr(pc, "publish_event", rec)
    monkeypatch.setattr(phb, "publish_event", rec)


def _cleanup_events(ctx, monkeypatch):
    """运行 ``_cleanup_display`` 并返回记录的 PhaseDone 阶段序列。"""
    rec = _EventRecorder()
    _patch_publishers(monkeypatch, rec)
    asyncio.run(AsyncStreamPipeline()._cleanup_display(ctx))
    return rec


class TestSegmentEndPublished:
    """正常收尾必发 segment_end；中断不发。"""

    def test_content_only_publishes_segment_end(self, monkeypatch):
        """纯回答（无推理、无工具）结束也发 segment_end。"""
        ctx = StreamContext("m", None, "assistant", True)
        ctx.content_full = "回答内容"
        rec = _cleanup_events(ctx, monkeypatch)
        assert "content" in rec.phase_done_phases
        assert "segment_end" in rec.phase_done_phases

    def test_reasoning_only_publishes_segment_end(self, monkeypatch):
        """仅推理（无回答、无工具）结束发 segment_end。"""
        ctx = StreamContext("m", None, "assistant", True)
        ctx.reasoning_full = "思考内容"
        rec = _cleanup_events(ctx, monkeypatch)
        assert "reasoning" in rec.phase_done_phases
        assert "segment_end" in rec.phase_done_phases

    def test_tool_calls_publish_segment_end(self, monkeypatch):
        """有工具调用时仍发 segment_end（原有语义保持）。"""
        ctx = StreamContext("m", None, "assistant", True)
        ctx.reasoning_full = "思考"
        ctx.tool_calls_map = {0: {"id": "c1", "name": "ls", "arguments": "{}"}}
        rec = _cleanup_events(ctx, monkeypatch)
        assert "segment_end" in rec.phase_done_phases

    def test_interrupted_skips_segment_end(self, monkeypatch):
        """ESC 中断不发送 segment_end（不完整内容不应触发完成信号）。"""
        ctx = StreamContext("m", None, "assistant", True)
        ctx.content_full = "回答"
        ctx.esc_interrupted = True
        rec = _cleanup_events(ctx, monkeypatch)
        assert "segment_end" not in rec.phase_done_phases

    def test_tracker_interrupted_skips_segment_end(self, monkeypatch):
        """工具参数接收中断不发送 segment_end。"""
        ctx = StreamContext("m", None, "assistant", True)
        ctx.content_full = "回答"
        ctx.tool_calls_map = {0: {"id": "c1", "name": "ls", "arguments": "{"}}
        ctx.tracker._interrupted = True
        rec = _cleanup_events(ctx, monkeypatch)
        assert "segment_end" not in rec.phase_done_phases

    def test_empty_stream_skips_segment_end(self, monkeypatch):
        """全空流（无产出）不发 segment_end（无内容可收尾）。"""
        ctx = StreamContext("m", None, "assistant", True)
        rec = _cleanup_events(ctx, monkeypatch)
        assert "segment_end" not in rec.phase_done_phases


# ═══════════════════════════════════════════════════════════
# 2. AppModel.finish_stream_render：渲染出所有 + 清空流式指示
# ═══════════════════════════════════════════════════════════

class TestFinishStreamRender:
    """收尾渲染器：刷出残差、关闭块、幂等。"""

    def _streaming_model(self) -> AppModel:
        m = AppModel()
        m.width = 60
        apply_cmd(m, ReasoningCmd(text="思考第一行"))
        apply_cmd(m, ContentCmd(text="回答内容"))
        return m

    def test_renders_unclosed_tail(self):
        """未闭合尾（最后一段无尾换行）在收尾时全部渲染出来。"""
        m = self._streaming_model()
        before = sum(len(b.lines) for b in m.blocks)
        m.finish_stream_render()
        after = sum(len(b.lines) for b in m.blocks)
        assert after > before, "收尾应把渲染器残差（未闭合尾）渲染成行"
        text = "\n".join(
            "".join(r.text for r in ln.runs) for b in m.blocks for ln in b.lines
        )
        assert "回答内容" in text

    def test_closes_both_channels(self):
        """收尾后推理/内容通道均关闭（流式指示清空的判定条件）。"""
        m = self._streaming_model()
        m.finish_stream_render()
        kinds = {b.kind: b for b in m.blocks}
        assert kinds["reasoning"].closed is True
        assert kinds["content"].closed is True
        # content 通道关闭标志（ensure_content 返回 None → 不再接收流式追加）
        assert m.content_closed is True
        assert m.content_renderer is None
        assert m.reasoning_renderer is None

    def test_streaming_indicator_cleared(self):
        """块 closed 后角色头回退静态（不再显示 spinner 帧）。"""
        from src.tui.app._model_helpers import _role_header_runs
        from src.tui.core._fx import SPINNER_FRAMES

        m = self._streaming_model()
        content_block = next(b for b in m.blocks if b.kind == "content")
        # 收尾前：live 角色头为 spinner 帧字符
        live_runs = _role_header_runs(content_block, live=True)
        assert live_runs[0].text[1] in SPINNER_FRAMES
        m.finish_stream_render()
        # 收尾后：静态「💬 回答」（spinner 已清空）
        static_runs = _role_header_runs(content_block, live=True)
        assert "回答" in static_runs[0].text
        assert static_runs[0].text[1] not in SPINNER_FRAMES

    def test_live_content_predicate_false_after_close(self):
        """chat_view 的 live content 判定（流式末尾 spinner 挂载条件）为假。"""
        # 用 ``\n\n`` 闭合一段，使块内已有渲染行（live 判定成立的完整条件）
        m2 = AppModel()
        m2.width = 60
        apply_cmd(m2, ContentCmd(text="回答第一行\n\n第二段"))
        content_block = next(b for b in m2.blocks if b.kind == "content")
        assert (content_block.kind == "content"
                and not content_block.closed
                and len(content_block.lines) > 0)
        m2.finish_stream_render()
        assert not (content_block.kind == "content"
                    and not content_block.closed
                    and len(content_block.lines) > 0)

    def test_idempotent(self):
        """重复收尾幂等（渲染行数稳定、无异常）。"""
        m = self._streaming_model()
        m.finish_stream_render()
        committed = len(m.committed_lines)
        m.finish_stream_render()
        m.finish_stream_render()
        assert len(m.committed_lines) == committed

    def test_no_channels_opened_is_safe(self):
        """从未打开流式通道时收尾零成本、无异常。"""
        m = AppModel()
        m.width = 60
        m.finish_stream_render()
        assert m.blocks == []
        assert m.content_closed is True


# ═══════════════════════════════════════════════════════════
# 3. apply：PhaseDoneCmd(segment_end) 驱动收尾
# ═══════════════════════════════════════════════════════════

class TestApplySegmentEnd:
    """``PhaseDoneCmd(phase="segment_end")`` → 收尾流式 markdown。"""

    def _streaming_model(self) -> AppModel:
        m = AppModel()
        m.width = 60
        apply_cmd(m, ReasoningCmd(text="思考内容"))
        apply_cmd(m, ContentCmd(text="回答内容"))
        return m

    def test_segment_end_closes_and_renders(self):
        m = self._streaming_model()
        apply_cmd(m, PhaseDoneCmd(phase="segment_end"))
        assert all(b.closed for b in m.blocks)
        text = "\n".join(
            "".join(r.text for r in ln.runs) for b in m.blocks for ln in b.lines
        )
        assert "思考内容" in text and "回答内容" in text

    def test_segment_end_after_phase_done_is_noop(self):
        """分阶段关闭（reasoning/content）已收尾时，segment_end 幂等。"""
        m = self._streaming_model()
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        committed = len(m.committed_lines)
        apply_cmd(m, PhaseDoneCmd(phase="segment_end"))
        assert len(m.committed_lines) == committed
        assert all(b.closed for b in m.blocks)


# ═══════════════════════════════════════════════════════════
# 4. 端到端：process 收尾后各阶段命令顺序正确
# ═══════════════════════════════════════════════════════════

class TestProcessEndToEnd:
    """真实 ``process`` 链路：收尾事件在内容 chunk 之后到达。"""

    def test_segment_end_arrives_after_content(self, monkeypatch):
        rec = _EventRecorder()
        _patch_publishers(monkeypatch, rec)
        ctx = StreamContext("test-model", None, "assistant", True)
        chunks = [{"choices": [{"delta": {"content": "a" * 20}}]} for _ in range(3)]

        async def gen():
            for ch in chunks:
                yield ch

        asyncio.run(AsyncStreamPipeline().process(ctx, gen(), True))
        names = [e[0] for e in rec.events]
        assert "ContentChunkEvent" in names
        assert rec.phase_done_phases[-1] == "segment_end"
        # 收尾事件必在最后一帧内容事件之后
        last_content = max(i for i, n in enumerate(names) if n == "ContentChunkEvent")
        seg_index = max(
            i for i, (n, kw) in enumerate(rec.events)
            if n == "PhaseDoneEvent" and kw.get("phase") == "segment_end"
        )
        assert seg_index > last_content
