"""2026-10-23 修复回归测试（渲染自愈 / 错误帧旁路 / 并发 / 焦点）。

覆盖本轮修复（问题清单见交付说明）：
  1. 渲染器不变量损坏 → 自愈重置 + 全量重绘（不再永久冻结）；
  2. ``mark_corrupted`` → 下一次成功帧清屏重绘（ED2）；
  3. 会话错误帧旁路直写（不替换文档模型 / 不经过 InkRenderer.render）；
  4. 错误恢复后能重新渲染正常内容；
  5. deferred 命令跨线程 swap/prepend 不丢；
  6. ``flush`` 硬上限可配且按剩余时间分段等待（停滞时快速返回）；
  7. memo 短路不再丢失 focus id / focus_active；
  8. 组件停止调用 useFocus 后 focus id 与本帧 active 同步清理；
  9. 事件循环线程上的内容命令背压不再阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import io
import threading
import time

import pytest

from src.tui._config import TuiConfig
from src.tui._const import ContentCmd, RenderCmd, RenderCommand, WriteLineCmd
from src.tui.ink import Box, Text, h, memo
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.hooks import use_input, useFocus
from src.tui.ink.output import Frame, Line
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.renderer import InkRenderer, RendererStateError
from src.tui.ink.session import InkSession


class _WidthCache:
    def __init__(self, width: int = 40, height: int = 10) -> None:
        self._w = width
        self._h = height

    def get_width(self) -> int:
        return self._w

    def get_height(self) -> int:
        return self._h

    def poll(self, ttl: float) -> None:
        return None

    def force_refresh(self) -> None:
        return None


def _frame(text: str) -> Frame:
    line = Line()
    line.append(text)
    return Frame([line])


# ── 1. 渲染器自愈 ──────────────────────────────────────


def test_invariant_violation_raises_state_error() -> None:
    renderer = InkRenderer(stream=io.StringIO(), height=10)
    renderer._buf_h = -1
    with pytest.raises(RendererStateError):
        renderer._assert_renderer_invariants()


def test_renderer_recovers_from_corrupted_state() -> None:
    out = io.StringIO()
    renderer = InkRenderer(stream=out, height=10)
    renderer.render(_frame("hello"))
    # 模拟记账损坏（修复前：每帧抛 RuntimeError，界面永久静默冻结）
    renderer._buf_h = -1
    renderer.render(_frame("hello world"))
    assert renderer.prev_frame is not None
    assert renderer.prev_frame.lines[0].plain == "hello world"
    assert renderer._buf_h >= 0
    assert "hello world" in out.getvalue()


def test_mark_corrupted_forces_full_repaint() -> None:
    out = io.StringIO()
    renderer = InkRenderer(stream=out, height=10)
    renderer.render(_frame("a"))
    out.seek(0)
    out.truncate(0)

    renderer.mark_corrupted()
    assert renderer.prev_frame is None
    renderer.render(_frame("b"))

    text = out.getvalue()
    assert "\x1b[2J" in text  # 全量重绘前清可见区
    assert "b" in text


# ── 2. 错误帧旁路 + 恢复 ────────────────────────────────


def test_render_failure_frame_bypasses_renderer_state() -> None:
    mixin = object.__new__(_SessionFrameMixin)
    out = io.StringIO()
    mixin._width_cache = _WidthCache()
    mixin._ink_renderer = InkRenderer(stream=out, height=10)
    mixin._last_frame = object()
    mixin._last_frame_lines = 3
    mixin._dirty = False

    mixin._render_failure_frame(RuntimeError("boom"))

    text = out.getvalue()
    assert "render error" in text
    assert "RuntimeError" in text
    assert "boom" in text
    # 旁路直写不进入帧模型；损坏标记让下一次成功帧全量重绘
    assert mixin._ink_renderer.prev_frame is None
    assert mixin._last_frame is None
    assert mixin._dirty is True


def test_session_recovers_after_render_error() -> None:
    out = io.StringIO()
    state = {"fail": True}

    def build(_model, _width):
        if state["fail"]:
            raise RuntimeError("boom")
        return h(Text, None, "recovered")

    session = InkSession(
        model=_SimpleModel(),
        build_tree=build,
        width_cache=_WidthCache(),
        stream=out,
    )
    session._render_frame()
    assert "render error" in out.getvalue()
    assert session._ink_renderer.prev_frame is None

    state["fail"] = False
    out.seek(0)
    out.truncate(0)
    session._render_frame()

    text = out.getvalue()
    assert "recovered" in text
    assert "\x1b[2J" in text  # 恢复走全量重绘
    assert session._ink_renderer.prev_frame is not None


# ── 3. deferred 命令并发 ───────────────────────────────


def test_deferred_prepend_and_swap_are_thread_safe() -> None:
    session = object.__new__(InkSession)
    session._deferred_commands = []
    session._deferred_lock = threading.Lock()
    producers = 4
    per_producer = 250

    def _produce(tag: int) -> None:
        for i in range(per_producer):
            session._prepend_deferred([(tag, i)])

    threads = [threading.Thread(target=_produce, args=(t,)) for t in range(producers)]
    for t in threads:
        t.start()
    collected: list = []
    while any(t.is_alive() for t in threads):
        collected.extend(session._swap_deferred())
    for t in threads:
        t.join()
    collected.extend(session._swap_deferred())

    assert len(collected) == producers * per_producer


# ── 4. flush 硬上限 ────────────────────────────────────


def test_flush_hard_timeout_bounds_stalled_drain() -> None:
    session = InkSession(
        model=_SimpleModel(),
        build_tree=lambda model, width: h(Text, None, "x"),
        width_cache=_WidthCache(),
        stream=io.StringIO(),
    )
    stop = threading.Event()

    def _stall() -> None:
        stop.wait(5.0)

    thread = threading.Thread(target=_stall, daemon=True)
    thread.start()
    session._render_thread = thread
    session._render_running = True
    # 未 task_done 的低优先级命令 → queue.join() 阻塞，走硬上限路径
    session._cmd_queue.put((3, 0, WriteLineCmd(text="pending")))

    started = time.monotonic()
    session.flush(timeout=0.05, hard_timeout=0.2)
    elapsed = time.monotonic() - started

    session._render_running = False
    stop.set()
    thread.join(timeout=1.0)
    assert elapsed < 3.0


# ── 5. 焦点 / memo ─────────────────────────────────────


def test_memo_preserves_focus_across_skipped_frames() -> None:
    def Inner(props):
        useFocus({"id": "a", "autoFocus": True})
        use_input(lambda event: True)
        return h(Text, None, "inner")

    InnerMemo = memo(Inner)

    def App(props):
        return h(Box, None, h(InnerMemo, {"x": 1}))

    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    ctx = reconciler.hook_context
    for _ in range(3):
        reconciler.render(root, h(App, None), 40, 10)

    assert list(ctx.focus_ids) == ["a"]
    assert ctx.focus_active == "a"


def test_focus_ids_cleared_when_use_focus_stops() -> None:
    def Comp(props):
        if props.get("focus"):
            useFocus({"id": "b", "autoFocus": True})
        return h(Text, None, "x")

    reconciler = Reconciler(None)
    root = Reconciler.create_root()
    ctx = reconciler.hook_context
    reconciler.render(root, h(Comp, {"focus": True}), 40, 10)
    assert list(ctx.focus_ids) == ["b"]
    assert ctx.focus_active == "b"

    reconciler.render(root, h(Comp, {"focus": False}), 40, 10)
    assert list(ctx.focus_ids) == []
    assert ctx.focus_active is None


# ── 6. 事件循环线程背压不阻塞 ──────────────────────────


def test_put_no_drop_skips_blocking_in_async_loop() -> None:
    session = InkSession(
        model=_SimpleModel(),
        build_tree=lambda model, width: h(Text, None, "x"),
        width_cache=_WidthCache(),
        stream=io.StringIO(),
        config=TuiConfig(cmd_queue_maxsize=1),
    )
    session._render_running = True
    session._cmd_queue.put((2, 0, RenderCmd(cid=RenderCommand.PARSE_INFO)))

    async def _call() -> tuple[bool, float]:
        started = time.monotonic()
        ok = session._put_no_drop(2, ContentCmd(text="x"))
        return ok, time.monotonic() - started

    ok, elapsed = asyncio.run(_call())
    assert ok is False
    assert elapsed < 0.2
