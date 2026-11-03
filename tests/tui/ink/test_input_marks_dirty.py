"""输入处理置脏（2026-11-03 /models 编辑不刷新回归测试）。

背景：ModelView 等全屏视图声明 ``idle_static=True``（空闲帧复用），其输入
处理器直接修改外部状态（不经过 ``use_state``）。修复前输入消费没有任何
变更标记，``_should_reuse_idle_frame`` 会复用上一帧——按键后界面不刷新，
直到系统统计等偶发脏事件（≤2s）才补渲。

修复：``process_events`` 返回本帧是否处理过输入，会话 INPUT 阶段据此置
``_dirty``；无输入的静态帧仍可复用（不牺牲空闲性能）。
"""

from __future__ import annotations

import io

from src.tui._input_dispatcher import InputDispatcher
from src.tui.ink.element import TEXT, h
from src.tui.ink.hooks import use_fullscreen, use_input
from src.tui.ink.session import InkSession


class _Model:
    width = 80
    committed_lines: list = []
    status = None

    def reflow_committed(self, width: int) -> None:
        return None

    def refresh_running_tool_titles(self) -> None:
        return None

    def reset_display(self) -> None:
        return None


class _FakeInput:
    """最小 Input 桩：只实现会话 INPUT 阶段使用的 process_events。"""

    def __init__(self, processed: bool) -> None:
        self.processed = processed
        self.calls: list[float | None] = []

    def process_events(self, budget: float | None = None) -> bool:
        self.calls.append(budget)
        return self.processed


def _make_session() -> InkSession:
    return InkSession(
        model=_Model(),
        apply_cmd=lambda model, cmd: None,
        build_tree=lambda model, width: h(TEXT, {"children": "hello"}),
        stream=io.StringIO(),
    )


def test_dispatcher_process_events_reports_processed_input() -> None:
    """``process_events`` 返回值 = 本帧是否处理过输入。"""
    dispatcher = object.__new__(InputDispatcher)
    sequence = [True, True, False]
    dispatcher.read_stdin_once = lambda: sequence.pop(0)  # type: ignore[method-assign]

    assert dispatcher.process_events() is True
    assert sequence == []

    idle = object.__new__(InputDispatcher)
    idle.read_stdin_once = lambda: False  # type: ignore[method-assign]
    assert idle.process_events(budget=0.01) is False


def test_session_marks_dirty_when_input_processed() -> None:
    """INPUT 阶段处理过输入 → 置脏，禁止本帧空闲复用。"""
    session = _make_session()
    session._input = _FakeInput(True)
    session._dirty = False
    session._idle_ok = True

    session._phase_process_input()

    assert session._dirty is True
    # 进入 RENDER 阶段：脏标记被消费为「本帧不可复用」
    assert session._should_render() is True
    assert session._idle_ok is False
    assert session._should_reuse_idle_frame(True) is False


def test_session_keeps_idle_when_no_input() -> None:
    """无输入时不得凭空置脏——静态空闲帧复用保持有效。"""
    session = _make_session()
    session._input = _FakeInput(False)
    session._dirty = False
    session._idle_ok = True

    session._phase_process_input()

    assert session._dirty is False
    assert session._should_render() is True
    assert session._idle_ok is True


class _Event:
    """最小按键事件（字段与框架 KeyEvent 对齐）。"""

    def __init__(self, kind: str, char: str = "") -> None:
        self.kind = kind
        self.char = char
        self.modifier = 0
        self.kitty_bits = -1
        self.event_type = ""


class _RouterInput:
    """最小 Input 桩：经 input router 分发一次按键。"""

    def __init__(self, router_ref: dict) -> None:
        self._router_ref = router_ref

    def process_events(self, budget: float | None = None) -> bool:
        router = self._router_ref.get("router")
        if router is None:
            return False
        return bool(router(_Event("char", "x")))


def test_input_router_mutation_rebuilds_idle_static_frame() -> None:
    """端到端：idle_static 视图经 router 直接改外部状态后必须重建。

    ModelView 的字段编辑不经过 ``use_state``（直接写 ``ModelViewState``）。
    本用例用真实调和器 + input router 复现该路径：第一帧渲染旧值，输入帧
    经 router 改外部状态并被 ``_phase_process_input`` 置脏，下一帧必须
    重建出新值（修复前会复用旧帧，屏幕停留在旧值）。
    """
    stream = io.StringIO()
    state = {"text": "old"}

    def _view(_props) -> object:
        def _handle(_event) -> bool:
            state["text"] = "new"
            return True

        use_input(_handle, True)
        use_fullscreen(True)
        return h(TEXT, {"children": state["text"]})

    session = InkSession(
        model=_Model(),
        apply_cmd=lambda model, cmd: None,
        build_tree=lambda model, width: h(_view, None),
        stream=stream,
    )
    session.set_idle_frame_predicate(lambda: True)

    # 捕获 reconciler 每帧发布的 input router（与生产 InputDispatcher 同源）。
    router_ref: dict = {}
    orig_cb = session._reconciler.hook_context.input_router_callback
    assert orig_cb is not None

    def _capture(router) -> None:
        router_ref["router"] = router
        orig_cb(router)

    session._reconciler.hook_context.input_router_callback = _capture

    # 首帧：渲染 "old"，并进入可空闲复用状态。
    session._render_frame()
    assert "old" in stream.getvalue()
    assert session._should_render() is True
    assert session._idle_ok is True

    # 输入帧：router 直接改外部状态（不经 use_state）——会话必须置脏。
    session._input = _RouterInput(router_ref)
    session._phase_process_input()
    assert state["text"] == "new"
    assert session._dirty is True
    assert session._should_render() is True
    assert session._idle_ok is False

    # 重建帧：新值必须上屏（不得复用上一帧）。
    stream.seek(0)
    stream.truncate(0)
    session._render_frame()
    assert "new" in stream.getvalue()
