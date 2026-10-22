"""game_loop 单次调用：一帧只构建一次组件树、输入一次排空、外部不插帧。

覆盖 2026-10-23 用户需求：
  - render 线程恒定 60Hz（不改帧率），每拍 ``_drain_queue`` 一次调用完成
    全部阶段（输入 → 帧驱动 → 面板 → 系统 → 命令 → 渲染）；
  - 输入 ``process_events`` 单次调用排空全部输入（无事件/时间预算）；
  - 外部线程不再直接调用 ``_render_frame``（仅在 render 线程未运行 /
    suspend 时保留同步例外）。
"""

from __future__ import annotations

import io

from src.core.frame_tick import get_frame_tick_registry
from src.tui._input_dispatcher import InputDispatcher
from src.tui.ink.element import TEXT, h
from src.tui.ink.session import InkSession


class _Model:
    width = 80
    committed_lines: list = []
    status = None

    def __init__(self) -> None:
        self.reset_calls = 0
        self.flush_calls = 0

    def reflow_committed(self, width: int) -> None:
        return None

    def refresh_running_tool_titles(self) -> None:
        return None

    def reset_display(self) -> None:
        self.reset_calls += 1


def _make_session() -> tuple[InkSession, _Model, dict]:
    model = _Model()
    calls = {"build": 0}

    def build(_model, _width):
        calls["build"] += 1
        return h(TEXT, {"children": ["hello"]})

    session = InkSession(
        model=model,
        apply_cmd=lambda m, c: None,
        build_tree=build,
        stream=io.StringIO(),
    )
    return session, model, calls


def test_single_drain_completes_all_phases_in_one_call() -> None:
    session, _model, calls = _make_session()
    registry = get_frame_tick_registry()
    ticks: list[int] = []
    tick = lambda: ticks.append(1)  # noqa: E731 - 测试回调
    registry.register(tick)
    try:
        session._drain_queue()
    finally:
        registry.unregister(tick)

    # 一帧：组件树只构建一次；帧驱动回调恰好执行一次；帧号推进一次。
    assert calls["build"] == 1
    assert ticks == [1]
    assert session._frame_seq == 1


def test_process_events_drains_without_budget() -> None:
    class _Probe(InputDispatcher):
        def __init__(self) -> None:
            self.remaining = 10_000

        def read_stdin_once(self) -> bool:
            self.remaining -= 1
            return self.remaining > 0

    probe = _Probe()
    probe.process_events()

    assert probe.remaining == 0


def test_flush_input_router_does_not_render_on_caller_thread() -> None:
    session, _model, _calls = _make_session()
    rendered: list[int] = []
    session._render_frame = lambda: rendered.append(1)  # type: ignore[assignment]
    session._render_running = True
    try:
        result = session.flush_input_router(timeout=0.05)
    finally:
        session._render_running = False

    assert result is False
    assert rendered == []


def test_clear_screen_while_running_defers_to_next_frame() -> None:
    session, model, _calls = _make_session()
    rendered: list[int] = []
    session._render_frame = lambda: rendered.append(1)  # type: ignore[assignment]
    session._render_running = True
    session._await_frame_advance = lambda count=1, timeout=2.0: False  # type: ignore[assignment]
    try:
        session.clear_screen()
    finally:
        session._render_running = False

    assert rendered == []
    assert model.reset_calls == 0
    assert session._clear_pending is True


def test_clear_screen_without_render_thread_keeps_sync_exception() -> None:
    session, model, _calls = _make_session()
    rendered: list[int] = []
    session._render_frame = lambda: rendered.append(1)  # type: ignore[assignment]
    session._render_running = False

    session.clear_screen()

    assert rendered == [1]
    assert model.reset_calls == 1
    assert session._clear_pending is False


def test_request_clear_defers_terminal_write_to_frame() -> None:
    session, model, _calls = _make_session()
    rendered: list[int] = []
    session._render_frame = lambda: rendered.append(1)  # type: ignore[assignment]
    session._render_running = True

    session.request_clear()

    assert rendered == []
    assert session._clear_pending is True
    assert model.reset_calls == 0
