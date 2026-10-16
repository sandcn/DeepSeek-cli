"""渲染线程恒定 30Hz 回归测试（2026-10-07 用户需求）。

需求：「渲染线程任何时候都是 30hz 渲染，不能改变」。

覆盖：
  1. 帧率真源常量（``RENDER_HZ`` / ``RENDER_INTERVAL_SEC``）与配置默认值；
  2. ``TuiConfig`` 帧率不可改变（构造 / ``with_overrides`` 均被强制回 1/30），
     「空闲按需渲染」开关（``idle_render``）已移除；
  3. ``InkSession._should_render``：恒定渲染（每拍都渲染，不跳过、不提前，
     节拍由渲染循环保证）；密集重绘请求不会导致超频；
  4. ``InkSession._needs_animation``（按需渲染探测）已移除；
  5. ``render(maxFps=...)`` 不再改变帧率；
  6. 端到端：空闲会话持续运行产生的帧数 ≈ 30Hz（按需渲染时仅首帧）。
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
        assert RENDER_HZ == 30.0
        assert RENDER_INTERVAL_SEC == pytest.approx(1.0 / 30)

    def test_config_default_follows_source(self):
        cfg = TuiConfig.defaults()
        assert cfg.render_interval == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.spinner_tick_hz == pytest.approx(RENDER_HZ)
        assert cfg.drain_lock_timeout == pytest.approx(RENDER_INTERVAL_SEC)
        assert cfg.bottom_redraw_interval == pytest.approx(RENDER_INTERVAL_SEC)

    def test_frame_rate_cannot_be_changed(self):
        """构造 / 覆盖路径均无法改变帧率（强制回真源值）。"""
        assert TuiConfig(render_interval=0.5).render_interval == pytest.approx(
            RENDER_INTERVAL_SEC
        )
        cfg = TuiConfig.defaults().with_overrides(render_interval=1.0 / 5)
        assert cfg.render_interval == pytest.approx(RENDER_INTERVAL_SEC)

    def test_idle_render_switch_removed(self):
        assert not hasattr(TuiConfig.defaults(), "idle_render")


class TestShouldRenderAlways:
    """``_should_render`` 恒定渲染语义（不再依赖脏/动画，不跳过、不提前）。"""

    def _stub(self) -> InkSession:
        stub = object.__new__(InkSession)
        stub._config = TuiConfig.defaults()
        stub._bottom_redraw_requested = threading.Event()
        stub._dirty = False
        return stub

    def test_always_true(self):
        stub = self._stub()
        assert stub._should_render() is True
        assert stub._should_render() is True

    def test_redraw_request_consumed_without_skipping(self):
        stub = self._stub()
        stub._bottom_redraw_requested.set()
        assert stub._should_render() is True
        assert not stub._bottom_redraw_requested.is_set()

    def test_animation_probe_removed(self):
        assert not hasattr(InkSession, "_needs_animation")


class TestIdleRenderLoopAt30Hz:
    """端到端：空闲会话仍按 30Hz 持续渲染。"""

    def test_idle_session_renders_continuously(self):
        session = _make_session()
        session.start()
        try:
            time.sleep(0.5)
        finally:
            session.stop()
        frames = session._frame_seq
        # 恒定 30Hz：0.5s ≈ 15 帧（按需渲染时仅首帧 ~1 帧）
        assert frames >= 6, f"空闲渲染帧数过少（疑似按需渲染）：{frames}"
        assert frames <= 30, f"空闲渲染超频（疑似忙循环）：{frames}"

    def test_frequent_redraw_requests_do_not_exceed_30hz(self):
        """密集重绘请求（模拟高频 force）不导致超频——帧率恒 ≤30Hz。"""
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
        # 0.5s @30Hz ≈ 15 帧；若重绘请求能提前渲染则会远超（>100）
        assert frames <= 25, f"密集重绘请求导致超频：{frames}"


def test_render_max_fps_ignored(monkeypatch):
    """``render(maxFps=...)`` 保留参数但不再改变帧率。"""
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
