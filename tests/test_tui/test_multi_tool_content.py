"""多轮工具循环「思考/回答最后一行不显示」修复测试（2026-08-16）。

修复背景（用户报告：TUI 中思考跟回答如果最后一行没有换行符不会显示最后
一行）。根因分两层：

1. apply 层通道拒绝（``_do_content``/``_do_reasoning``）：
   工具调用时 tool_calls.py 在 ``content_full`` 非空时发布
   ``PhaseDone("content")`` → ``close_content`` 关闭内容通道；工具调用后
   模型继续输出最终回答，而 content.py 的 ``phase_answering_sent`` 每流只
   发布一次 MainPhase（``reopen_content`` 未触发）→ ``ensure_content``
   返回 None → **工具调用后的回答被整体丢弃**。推理通道同理
   （``phase_thinking_sent`` 只发布一次 MainPhase）。

2. pipeline 层 PhaseDone 去重（``publish_phase_done_once`` 每流至多一次）：
   即使通道自动重开，工具调用后新一轮内容结束时 ``publish_phase_done_once``
   因标志已置位幂等跳过 → ``close_reasoning``/``close_content`` 不再执行 →
   新一轮内容尾部（最后一行无换行符）滞留在解析器缓冲**永不渲染**。

修复：
  - ``apply._do_reasoning``/``_do_content``：通道关闭时自动重开接收新一轮
    内容（新块），不再整体丢弃；
  - ``pipeline_async`` 工具调用分支：重置 ``phase_done_*_sent`` 标志，使
    工具调用后的新一轮内容结束时能重新触发 PhaseDone 发布（关闭幂等，
    重复发布无害）；
  - ``pipeline_async._cleanup_display``：content done 发布不再要求
    ``not ctx.tool_calls_map``（工具调用时 content 为空 → 流结束时必发布
    一次；已发布过则幂等跳过）。

测试锁定：
  1. ``_do_content`` 在内容通道关闭后自动重开（工具调用后的回答不丢）；
  2. ``_do_reasoning`` 在推理通道关闭后自动重开（工具调用后的思考不丢）；
  3. 端到端场景 B（推理→内容→工具→回答，最后一行无换行符）最后一行显示；
  4. 端到端场景 A（推理→工具→回答）最后一行显示；
  5. ``_cleanup_display`` 在 tool_calls_map 非空时也发布 content done；
  6. 工具调用分支重置 phase_done 标志（新一轮内容可再次触发 PhaseDone）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.tui.app.model import AppModel, ReasoningState
from src.tui.app.apply import apply_cmd
from src.tui._const import (
    MainPhaseCmd,
    ReasoningCmd,
    ContentCmd,
    PhaseDoneCmd,
    ToolOpenCmd,
    ToolCloseCmd,
)
from src.api.stream.context import StreamContext
from src.api.stream.pipeline_async import AsyncStreamPipeline
from src.tui.events import DisplayEventBus


@pytest.fixture(autouse=True)
def _isolate_event_bus():
    """每个测试隔离 DisplayEventBus 单例（防止 publish_event 跨测试泄漏）。"""
    DisplayEventBus.reset_default()
    yield
    DisplayEventBus.reset_default()


def _block_texts(model: AppModel) -> list[tuple[str, list[str]]]:
    """blocks → [(kind, [行纯文本])]。"""
    return [
        (b.kind, ["".join(r.text for r in ln.runs) if hasattr(ln, "runs") else str(ln)
                  for ln in b.lines])
        for b in model.blocks
    ]


def _all_block_lines(model: AppModel) -> list[str]:
    """全部块的行纯文本（扁平）。"""
    return [
        "".join(r.text for r in ln.runs) if hasattr(ln, "runs") else str(ln)
        for b in model.blocks for ln in b.lines
    ]


# ═══════════════════════════════════════════════════════════
# 1. apply 层：通道关闭后自动重开
# ═══════════════════════════════════════════════════════════

def test_do_content_reopens_closed_channel():
    """内容通道关闭（content_closed）后内容仍被接收（工具调用后的回答不丢）。

    场景：推理 → 内容A → 工具调用（PhaseDone(content) 关闭通道）→ 内容B。
    修复前 ensure_content 返回 None → 内容B 被整体丢弃。
    """
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="思考\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    apply_cmd(m, MainPhaseCmd(phase="answering"))
    apply_cmd(m, ContentCmd(text="内容A\n\n"))
    # 工具调用：tool_calls.py 在 content_full 非空时发布 content done → 通道关闭
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    apply_cmd(m, ToolOpenCmd(tool_id="t1", tool_name="Bash", detail="pwd"))
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    assert m.content_closed is True

    # 工具调用后模型继续输出内容B（无新 MainPhaseCmd）→ 自动重开接收
    apply_cmd(m, ContentCmd(text="内容B第一段\n\n"))
    apply_cmd(m, ContentCmd(text="内容B最后一行"))
    # 已 flush 段落（空行分隔）应显示；最后一行（无换行）在 close 时 flush
    assert "内容B第一段" in _all_block_lines(m), "内容B应被接收（通道自动重开）"
    # 结束时发布 content done → close_content → 最后一行 flush 显示
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    assert m.content_closed is True
    assert "内容B最后一行" in _all_block_lines(m)


def test_do_reasoning_reopens_closed_channel():
    """推理通道关闭（CLOSED）后新推理被接收（工具调用后的思考不丢）。

    场景：第一轮思考 → 关闭 → 工具调用后新一轮思考（无新 MainPhaseCmd）。
    """
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="第一轮思考\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    assert m.reasoning_state == ReasoningState.CLOSED

    # 工具调用后新一轮思考 → 自动重开接收（最后一行在 close 时 flush）
    apply_cmd(m, ReasoningCmd(text="第二轮思考最后一行"))
    assert m.reasoning_state == ReasoningState.ACTIVE, "推理通道应自动重开"
    # 新一轮思考结束 → close → 最后一行 flush 显示
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    assert m.reasoning_state == ReasoningState.CLOSED
    assert "第二轮思考最后一行" in _all_block_lines(m)


def test_do_content_normal_channel_unchanged():
    """正常流程（通道未关闭）不自动重开（行为零回归）。"""
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="answering"))
    apply_cmd(m, ContentCmd(text="回答\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    # 关闭后无新内容：状态保持关闭
    assert m.content_closed is True
    apply_cmd(m, PhaseDoneCmd(phase="content"))
    assert m.content_closed is True


# ═══════════════════════════════════════════════════════════
# 2. 端到端：工具调用后回答最后一行（无换行符）显示
# ═══════════════════════════════════════════════════════════

def test_scene_b_tool_after_content_last_line_rendered():
    """场景 B（推理→内容A→工具→内容B）：内容B 最后一行无换行符也显示。

    修复前：内容B 被 ensure_content 拒绝整体丢弃；修复后：自动重开 + 新的
    PhaseDone → close_content → 最后一行 flush 显示。
    """
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="思考\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    apply_cmd(m, MainPhaseCmd(phase="answering"))
    apply_cmd(m, ContentCmd(text="内容A\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))  # 工具调用时发布 content done
    apply_cmd(m, ToolOpenCmd(tool_id="t1", tool_name="Bash", detail="pwd"))
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    # 内容B（工具调用后，最后一行无换行符）
    apply_cmd(m, ContentCmd(text="内容B第一段\n\n"))
    apply_cmd(m, ContentCmd(text="内容B最后一行没有换行符"))
    # 流结束：pipeline 重置标志后重新发布 content done（此处直接模拟）
    apply_cmd(m, PhaseDoneCmd(phase="content"))

    blocks = _block_texts(m)
    content_blocks = [lines for kind, lines in blocks if kind == "content"]
    assert len(content_blocks) >= 2, "应有两个 content 块（内容A + 内容B）"
    assert "内容B最后一行没有换行符" in _all_block_lines(m), (
        "内容B最后一行（无换行符）应显示"
    )
    assert m.content_closed is True


def test_scene_a_tool_before_content_last_line_rendered():
    """场景 A（推理→工具→回答）：回答最后一行无换行符也显示。

    工具调用时 content 为空（tool_calls.py 不发布 content done）→ 流结束时
    _cleanup_display 发布 → close_content → 最后一行 flush 显示。
    """
    m = AppModel()
    m.width = 80
    apply_cmd(m, MainPhaseCmd(phase="thinking"))
    apply_cmd(m, ReasoningCmd(text="思考\n\n"))
    apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
    apply_cmd(m, ToolOpenCmd(tool_id="t1", tool_name="Bash", detail="pwd"))
    apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
    apply_cmd(m, MainPhaseCmd(phase="answering"))
    apply_cmd(m, ContentCmd(text="回答\n\n"))
    apply_cmd(m, ContentCmd(text="最后一行没有换行符"))
    apply_cmd(m, PhaseDoneCmd(phase="content"))

    assert "最后一行没有换行符" in _all_block_lines(m), (
        "工具调用后回答的最后一行（无换行符）应显示"
    )


# ═══════════════════════════════════════════════════════════
# 3. pipeline 层：_cleanup_display 发布与标志重置
# ═══════════════════════════════════════════════════════════

def _make_ctx(**kwargs) -> StreamContext:
    display = MagicMock()
    return StreamContext(model="test", display=display, label="main", silent=True, **kwargs)


def test_cleanup_display_publishes_content_done_with_tool_calls():
    """_cleanup_display 在 tool_calls_map 非空时也发布 content done。

    场景：工具调用时 content 为空（tool_calls.py 未发布 content done），
    工具调用后内容出现。修复前 ``not ctx.tool_calls_map`` 阻止发布 →
    close_content 不执行 → 工具后内容尾部滞留。
    """
    from src.tui.events.event_types import PhaseDoneEvent

    bus = DisplayEventBus.get_default()
    received: list = []
    bus.subscribe(lambda e: received.append(e), event_type=PhaseDoneEvent)

    ctx = _make_ctx()
    ctx.content_full = "工具后的回答"
    ctx.tool_calls_map = {0: {"id": "t1", "name": "Bash", "arguments": "{}"}}
    ctx.reasoning_full = "思考"
    ctx.tracker.finalize = AsyncMock()

    pipe = AsyncStreamPipeline()
    with patch.object(pipe._reasoning_handler, "flush"), \
         patch.object(pipe._content_handler, "flush"):
        asyncio.run(pipe._cleanup_display(ctx))

    phases = [e.phase for e in received]
    assert "content" in phases, (
        f"tool_calls_map 非空时也应发布 content done，实际发布: {phases}"
    )


def test_cleanup_display_publishes_content_done_without_tool_calls():
    """_cleanup_display 无工具调用时发布 content done（原行为不回归）。"""
    from src.tui.events.event_types import PhaseDoneEvent

    bus = DisplayEventBus.get_default()
    received: list = []
    bus.subscribe(lambda e: received.append(e), event_type=PhaseDoneEvent)

    ctx = _make_ctx()
    ctx.content_full = "回答"
    ctx.tracker.finalize = AsyncMock()

    pipe = AsyncStreamPipeline()
    with patch.object(pipe._reasoning_handler, "flush"), \
         patch.object(pipe._content_handler, "flush"):
        asyncio.run(pipe._cleanup_display(ctx))

    phases = [e.phase for e in received]
    assert "content" in phases, f"无工具调用时应发布 content done，实际: {phases}"


def test_tool_call_resets_phase_done_flags():
    """工具调用分支重置 phase_done_*_sent 标志（新一轮内容可再次触发 PhaseDone）。

    驱动 process() 完整流程：推理 → 工具调用 → 内容。工具调用分支执行后，
    标志被重置——否则工具调用后的内容结束时 publish_phase_done_once 幂等
    跳过，close_content 不再执行（尾部滞留）。验证：流结束时（_cleanup_display）
    工具调用后的内容仍能触发 content done 发布。
    """
    from src.tui.events.event_types import PhaseDoneEvent

    async def _run():
        bus = DisplayEventBus.get_default()
        received: list = []
        bus.subscribe(lambda e: received.append(e), event_type=PhaseDoneEvent)

        ctx = _make_ctx()
        pipe = AsyncStreamPipeline()
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "思考"}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "t1",
                 "function": {"name": "Bash", "arguments": '{"command":"pwd"}'}},
            ]}}]},
            {"choices": [{"delta": {"content": "回答"}}]},
        ]

        async def _iter():
            for c in chunks:
                yield c

        ctx.tracker.finalize = AsyncMock()

        with patch(
            "src.api.stream.pipeline_async._interruptible_iter_async",
            side_effect=lambda it, c: _iter(),
        ), patch(
            "src.api.stream.pipeline_async.is_interrupted_async",
            new=AsyncMock(return_value=False),
        ), patch.object(pipe._tool_calls_handler, "handle", new=AsyncMock()):
            await pipe.process(ctx, None, silent=True)

        # 内容累积完整（未被 pipeline 丢弃）
        assert ctx.content_full == "回答"
        assert ctx.reasoning_full == "思考"
        # 工具调用后的内容触发 content done（标志已重置 → cleanup 发布）
        phases = [e.phase for e in received]
        assert "content" in phases, (
            f"工具调用后的内容应触发 content done，实际发布: {phases}"
        )

    asyncio.run(_run())


def test_publish_phase_done_once_idempotent_and_resettable():
    """publish_phase_done_once 幂等 + 标志重置后可再次发布（修复前提）。"""
    from src.tui.events.event_types import PhaseDoneEvent

    bus = DisplayEventBus.get_default()
    received: list = []
    bus.subscribe(lambda e: received.append(e), event_type=PhaseDoneEvent)

    ctx = _make_ctx()
    # 首次发布
    assert ctx.publish_phase_done_once("content") is True
    assert ctx.publish_phase_done_once("content") is False  # 幂等跳过
    # 重置标志后再次发布（工具调用分支语义）
    ctx.phase_done_content_sent = False
    assert ctx.publish_phase_done_once("content") is True
    assert ctx.phase_done_content_sent is True
    # 共发布 2 次（首次 + 重置后）
    assert len(received) == 2
    assert [e.phase for e in received] == ["content", "content"]
