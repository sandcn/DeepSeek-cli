"""空闲帧复用测试（P0-1 按需渲染后的语义）。

P0-1 后渲染线程空闲时**不再每拍渲染**；空闲帧复用只对「有帧被驱动」的
场景有意义（动画 tick / 宿主显式请求）。本文件保留原有覆盖点，断言改为：

  - 空闲无请求：几乎不渲染（按需）；
  - 动画/请求驱动帧时：``set_idle_frame_predicate`` 为 True 的静态拍复用
    上一帧（跳过组件树重建），但帧号照常推进；
  - 未注入预测函数：每次被驱动的帧都重建组件树；
  - 重绘请求后：下一帧必定重建（不复用陈旧帧）。
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


def _drive_frames(session: InkSession, count: int = 5) -> None:
    """直接驱动帧执行（绕过调度器），模拟动画 tick（非脏帧）。"""
    for _ in range(count):
        session._animating = True
        session._bottom_redraw_requested.clear()
        session._dirty = False
        session._drain_queue()
    session._animating = False


def test_idle_reuse_skips_rebuild_but_advances_frames():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: True)
    # 首帧建立 last_frame。
    _drive_frames(session, 1)
    before_frames = session._frame_seq
    before_calls = len(calls)
    # 后续静态帧：帧号推进但组件树不重建。
    _drive_frames(session, 5)
    assert session._frame_seq > before_frames
    assert len(calls) <= before_calls + 1, "静态帧仍在重建组件树"


def test_without_predicate_rebuilds_each_driven_frame():
    calls: list = []
    session = _make_session(calls)
    _drive_frames(session, 6)
    assert len(calls) >= 5, "未注入预测函数时应逐帧重建"


def test_dirty_state_forces_rebuild():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: True)
    _drive_frames(session, 1)
    before = len(calls)
    session._request_render()
    # 不经过 _drive_frames（其会清 dirty）；直接驱动一帧。
    session._animating = True
    session._drain_queue()
    session._animating = False
    assert len(calls) > before, "重绘请求后未重建组件树"


def test_predicate_false_disables_reuse():
    calls: list = []
    session = _make_session(calls)
    session.set_idle_frame_predicate(lambda: False)
    _drive_frames(session, 6)
    assert len(calls) >= 5, "预测函数为 False 时不得复用"


def test_predicate_exception_falls_back_to_rebuild():
    calls: list = []
    session = _make_session(calls)

    def _boom() -> bool:
        raise RuntimeError("bad predicate")

    session.set_idle_frame_predicate(_boom)
    _drive_frames(session, 4)
    assert len(calls) >= 3, "预测函数异常时应回退重建"


def test_idle_session_without_requests_does_not_spin():
    calls: list = []
    session = _make_session(calls)
    session.start()
    try:
        time.sleep(0.25)
    finally:
        session.stop()
    # 空闲按需：不应出现 60Hz 全量重建。
    assert len(calls) <= 4
    assert session._frame_seq <= 4
