"""渲染调度与输入轮询回归测试（变更驱动 + 60Hz 上限）。

覆盖：
  1. 帧率真源常量（``RENDER_HZ`` / ``RENDER_INTERVAL_SEC``）与配置默认值；
  2. ``TuiConfig`` 帧率不可降低（构造 / ``with_overrides`` 均被强制回 1/60）；
  3. ``InkSession._should_render``：变更驱动（脏/首帧/重绘才重建，空闲跳过）；
  4. ``InkSession._needs_animation`` 不存在；
  5. ``render(maxFps=...)`` 不改变节拍；
  6. 端到端：空闲会话不持续重建，但输入轮询仍按 60Hz 节拍执行；
  7. 输入处理：``_phase_process_input`` 每个节拍执行一次（与是否重建无关）。
"""

from __future__ import annotations

import io
import threading
import time

import pytest

from src.tui._config import RENDER_HZ, RENDER_INTERVAL_SEC, TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink import h, TEXT
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink.session import InkSession


def _make_session() -> InkSession:
    model = _SimpleModel()

    def _build_tree(m, w):
        return h(TEXT, {"children": "idle"})

    return InkSession(
        model=model,
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )


class TestFixedFrameRateSource:
    """帧率真源与配置默认值。"""

    def test_constants(self):
        assert RENDER_HZ == 60.0
        assert RENDER_INTERVAL_SEC == pytest.approx(1.0 / 60)

    def test_config_default_follows_source(self):
        cfg = TuiConfig.defaults()
        assert cfg.render_interval == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.spinner_tick_hz == pytest.approx(RENDER_HZ)
        assert cfg.drain_lock_timeout == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.bottom_redraw_interval == pytest.approx(RENDER_INTERVAL_SEC)

    def test_frame_rate_cannot_be_changed(self):
        """构造 / 覆盖路径均无法降低帧率（强制回真源值）。"""
        assert TuiConfig(render_interval=0.5).render_interval == pytest.approx(
            RENDER_INTERVAL_SEC
        )
        cfg = TuiConfig.defaults().with_overrides(
            render_interval=1.0 / 5,
            drain_lock_timeout=0.5,
            bottom_redraw_interval=0.5,
            spinner_tick_hz=5.0,
        )
        assert cfg.render_interval == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.drain_lock_timeout == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.bottom_redraw_interval == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.spinner_tick_hz == pytest.approx(RENDER_HZ)

    def test_idle_render_switch_removed(self):
        assert not hasattr(TuiConfig.defaults(), "idle_render")


class _SizeCache:
    def get_width(self) -> int:
        return 80

    def get_height(self) -> int:
        return 24


class TestShouldRenderOnDemand:
    """``_should_render`` 变更驱动语义：有变更才重建，空闲跳过。"""

    def _stub(self) -> InkSession:
        stub = object.__new__(InkSession)
        stub._config = TuiConfig.defaults()
        stub._bottom_redraw_requested = threading.Event()
        stub._dirty = False
        stub._last_frame = object()          # 已有上一帧
        stub._resize_pending = False
        stub._width_cache = _SizeCache()
        stub._last_render_width = 80
        stub._last_render_height = 24
        stub._idle_frame_predicate = lambda: True   # 宿主判定静态
        stub._frame_flush_waiters = []
        return stub

    def test_idle_skips_rebuild(self):
        stub = self._stub()
        assert stub._should_render() is False
        assert stub._should_render() is False

    def test_dirty_renders(self):
        stub = self._stub()
        stub._dirty = True
        assert stub._should_render() is True
        assert stub._dirty is False

    def test_redraw_request_consumed_and_renders(self):
        stub = self._stub()
        stub._bottom_redraw_requested.set()
        assert stub._should_render() is True
        assert not stub._bottom_redraw_requested.is_set()

    def test_first_frame_renders(self):
        stub = self._stub()
        stub._last_frame = None
        assert stub._should_render() is True

    def test_host_time_driven_predicate_renders(self):
        stub = self._stub()
        stub._idle_frame_predicate = lambda: False   # 非静态
        assert stub._should_render() is True
        stub._idle_frame_predicate = lambda: True    # 静态
        assert stub._should_render() is False

    def test_without_predicate_keeps_rendering(self):
        """无预测函数（独立 render()/测试）保持节拍渲染兼容。"""
        stub = self._stub()
        stub._idle_frame_predicate = None
        assert stub._should_render() is True

    def test_animation_probe_removed(self):
        assert not hasattr(InkSession, "_needs_animation")


class TestIdleRenderLoopAt60Hz:
    """端到端：空闲会话跳过重建，但节拍与输入轮询仍按 60Hz 运行。"""

    def test_idle_session_skips_rebuild(self):
        session = _make_session()
        session.set_idle_frame_predicate(lambda: True)
        session.start()
        try:
            time.sleep(0.5)
        finally:
            session.stop()
        frames = session._frame_seq
        # 无变更：仅首帧（及少量系统统计更新），不再 60Hz 全量重建
        assert frames <= 5, f"空闲帧仍在持续重建（疑似恒定拉模式）：{frames}"
        # 输入轮询照常（每节拍一次），事件计数不因跳过重建而减少
        assert session.metrics()["frames"] == frames

    def test_frequent_redraw_requests_do_not_exceed_60hz(self):
        """密集重绘请求（模拟高频 force）不导致超频——帧率恒 ≤60Hz。"""
        session = _make_session()
        session.start()
        stop = time.monotonic() + 0.5
        try:
            while time.monotonic() < stop:
                session._request_render()
                time.sleep(0.002)
        finally:
            session.stop()
        frames = session._frame_seq
        # 0.5s @60Hz ≈ 30 帧；若重绘请求能提前渲染则会远超（>100）
        assert frames <= 50, f"密集重绘请求导致超频：{frames}"


def test_input_phase_runs_every_render_tick_at_60hz():
    """INPUT 阶段每个节拍都调用 ``process_events``——与是否重建无关。"""
    session = _make_session()
    session.set_idle_frame_predicate(lambda: True)
    calls: list[int] = []

    class _Input:
        def process_events(self) -> int:
            calls.append(1)
            return 0

    session._input = _Input()
    started = time.monotonic()
    session.start()
    try:
        time.sleep(0.35)
    finally:
        session.stop()
    elapsed = time.monotonic() - started
    expected_ticks = elapsed / TuiConfig.defaults().render_interval
    # 输入轮询按 60Hz 节拍执行（允许调度抖动）
    assert len(calls) >= expected_ticks * 0.6, (
        f"输入轮询被降采样：calls={len(calls)} expected≈{expected_ticks:.1f}"
    )
    # 空闲时不再逐帧重建
    assert session._frame_seq <= 5


def test_render_max_fps_ignored(monkeypatch):
    """``render(maxFps=...)`` 保留参数但不再降低帧率。"""
    from src.tui.ink import render

    captured: list = []
    real_init = InkSession.__init__

    def _spy(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        captured.append(self)

    monkeypatch.setattr(InkSession, "__init__", _spy)
    buf = io.StringIO()
    ctrl = render(h(TEXT, {"children": "x"}), stdout=buf, width=20, maxFps=1)
    try:
        time.sleep(0.1)
    finally:
        ctrl["unmount"]()
    assert captured
    assert captured[-1]._config.render_interval == pytest.approx(RENDER_INTERVAL_SEC)
