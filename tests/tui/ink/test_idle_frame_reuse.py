"""空闲帧复用 / 变更驱动调度测试。

``InkSession._should_render`` 采用变更驱动：无脏标记、无动画、无 resize、
宿主判定静态时**跳过重建**（帧号不推进）；宿主预测为「非静态」或存在脏
标记时正常重建。输入轮询仍按 60Hz 节拍独立执行。
"""

from __future__ import annotations

import io
import time

from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink import h, TEXT
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink.session import InkSession


def _make_session(build_calls: list) -> InkSession:
    model = _SimpleModel()

    def _build_tree(m, w):
        build_calls.append(1)
        return h(TEXT, {"children": "idle"})

    return InkSession(
        model=model,
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )


def test_idle_reuse_skips_rebuild():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: True)
    session.start()
    try:
        time.sleep(0.4)
    finally:
        session.stop()
    frames = session._frame_seq
    # 空闲：只渲染首帧（及少量系统统计脏帧），不逐帧重建
    assert frames <= 4, f"空闲仍在逐帧重建：{frames}"
    assert len(calls) <= 4, f"空闲拍仍在重建组件树：{len(calls)}"
    assert len(calls) == frames


def test_without_predicate_renders_each_tick():
    calls: list = []
    session = _make_session(calls)
    session.start()
    try:
        time.sleep(0.3)
    finally:
        session.stop()
    frames = session._frame_seq
    # 未注入预测函数：保持兼容（按节拍渲染）
    assert frames >= 4
    assert len(calls) >= frames - 1


def test_dirty_state_forces_rebuild():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: True)
    session.start()
    try:
        time.sleep(0.2)
        before = len(calls)
        session._request_render()          # 置脏 + 请求重绘
        time.sleep(0.2)
    finally:
        session.stop()
    assert len(calls) > before, "重绘请求后未重建组件树"


def test_predicate_false_disables_reuse():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: False)
    session.start()
    try:
        time.sleep(0.3)
    finally:
        session.stop()
    assert len(calls) >= session._frame_seq - 1


def test_predicate_exception_falls_back_to_rebuild():
    calls: list = []
    session = _make_session(calls)

    def _boom() -> bool:
        raise RuntimeError("bad predicate")

    session.set_idle_frame_predicate(_boom)
    session.start()
    try:
        time.sleep(0.25)
    finally:
        session.stop()
    # 预测异常 → 视为不空闲（正常重建，绝不静默复用陈旧帧）
    assert len(calls) >= session._frame_seq - 1


def test_should_reuse_requires_previous_frame():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: True)
    # 尚未渲染过任何帧 → 不复用
    assert session._should_reuse_idle_frame() is False


def test_app_predicate_static_and_active_states():
    from src.tui._assembly_steps import _make_idle_frame_predicate

    model = _SimpleModel()
    predicate = _make_idle_frame_predicate(model, session=None)
    assert predicate() is True

    model.status = type("S", (), {"status_active": True})()
    assert predicate() is False

    model.status = type("S", (), {"status_active": False})()
    model.parse_line = object()
    assert predicate() is False

    model.parse_line = None
    model.tool_boxes = {"t": object()}
    assert predicate() is False

    model.tool_boxes = {}
    model.fullscreen = "trace"
    assert predicate() is False


def _make_command_session(build_calls: list) -> InkSession:
    """构造带命令应用回调的会话（模型支持 append_committed）。"""
    from src.tui.app.apply import apply_cmd

    class _Model(_SimpleModel):
        def __init__(self):
            self.committed_lines: list = []

        def append_committed(self, kind, lines):
            self.committed_lines.extend(lines)
            return None

    model = _Model()

    def _build_tree(m, w):
        build_calls.append(1)
        return h(TEXT, {"children": "x"})

    return InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )


def test_applied_command_forces_rebuild_instead_of_stale_reuse():
    """命令应用拍必须重建组件树（不得复用陈旧帧）。

    回归场景：``--load`` 启动后输入 ``exit``，退出提示「再见 恢复: …」经
    ``write_line``（WRITE_LINE 命令）投递——修复前命令在 DRAIN_COMMANDS
    阶段已出队（空闲帧复用的「队列非空」判据失效），``_dirty`` 又未置位 →
    本拍复用上一帧，提示行永不渲染（进程随即退出）。
    """
    from src.tui._const import WriteLineCmd

    calls: list = []
    session = _make_command_session(calls)
    session.set_idle_frame_predicate(lambda: True)

    session._drain_queue()            # 首帧：无上一帧 → 重建
    first = len(calls)
    assert first == 1

    session._drain_queue()            # 空闲拍：复用上一帧（不重建）
    assert len(calls) == first, "空闲拍不应重建组件树"

    session.push_cmd(WriteLineCmd(text="hello-command"))
    session._drain_queue()            # 命令应用拍：模型变更 → 必须重建

    assert len(calls) > first, "命令应用后复用了陈旧帧（提示行不会渲染）"
    assert session._model.committed_lines, "命令未应用到模型"


def test_stale_frame_reuse_regression_guard_without_command():
    """无命令的空闲拍仍复用（确认重建只由模型变更触发，不误伤性能优化）。"""
    calls: list = []
    session = _make_command_session(calls)
    session.set_idle_frame_predicate(lambda: True)

    session._drain_queue()
    session._drain_queue()
    session._drain_queue()

    assert len(calls) == 1


def test_placeholder_fading_detection():
    from src.tui._assembly_steps import _placeholder_fading

    class _Fiber:
        _placeholder_fade_key = None

    class _Session:
        _input_fiber = _Fiber()

    session = _Session()
    assert _placeholder_fading(session) is False
    _Fiber._placeholder_fade_key = ("ph", time.monotonic())
    assert _placeholder_fading(session) is True
    _Fiber._placeholder_fade_key = ("ph", time.monotonic() - 10**6)
    assert _placeholder_fading(session) is False
