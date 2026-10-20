"""渲染调度回归测试（P0-1 按需渲染 + 60Hz 上限）。

历史（2026-10-20）曾要求「渲染线程恒定 60Hz 全量渲染」；该契约导致空闲
CPU 常驻、大文档每帧 O(文档) 全量重建，已被 P0-1 架构修复取代：

  - 帧率真源仍为 60Hz（``RENDER_HZ`` / ``RENDER_INTERVAL_SEC``），但它是
    **最大帧率**，不是空闲节拍；
  - 空闲时渲染线程阻塞在调度器 Event 上，不重建组件树、不做全量渲染；
  - 命令/输入/重绘/动画请求到达时按需唤醒，连续请求不超过 60Hz；
  - 输入读取由 ``InputPump`` 独立线程驱动（不依赖渲染节拍）。

本文件保留原有用例名/覆盖点，断言改为新契约。
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


def _make_session(build_calls: list | None = None) -> InkSession:
    model = _SimpleModel()
    calls = build_calls if build_calls is not None else []

    def _build_tree(m, w):
        calls.append(1)
        return h(TEXT, {"children": "idle"})

    return InkSession(
        model=model,
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )


class TestFixedFrameRateSource:
    """帧率真源与配置默认值（60Hz = 最大帧率）。"""

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
        """构造 / 覆盖路径均无法改变 60Hz 上限。"""
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


class TestShouldRenderOnDemand:
    """``_should_render`` 按需语义：脏/重绘/动画/首帧才渲染。"""

    def _stub(self) -> InkSession:
        stub = object.__new__(InkSession)
        stub._config = TuiConfig.defaults()
        stub._bottom_redraw_requested = threading.Event()
        stub._dirty = False
        stub._animating = False
        stub._force_render = False
        return stub

    def test_idle_does_not_render(self):
        stub = self._stub()
        assert stub._should_render() is False
        assert stub._should_render() is False

    def test_dirty_renders(self):
        stub = self._stub()
        stub._dirty = True
        assert stub._should_render() is True
        # 消费后回到空闲。
        assert stub._should_render() is False

    def test_redraw_request_consumed(self):
        stub = self._stub()
        stub._bottom_redraw_requested.set()
        assert stub._should_render() is True
        assert not stub._bottom_redraw_requested.is_set()

    def test_first_frame_forced(self):
        stub = self._stub()
        stub._force_render = True
        assert stub._should_render() is True
        assert stub._force_render is False

    def test_animation_renders(self):
        stub = self._stub()
        stub._animating = True
        assert stub._should_render() is True


class TestIdleRenderLoop:
    """端到端：空闲只渲染首帧；请求后按需再渲染。"""

    def test_idle_session_renders_first_frame_only(self):
        calls: list = []
        session = _make_session(calls)
        session.start()
        try:
            time.sleep(0.35)
        finally:
            session.stop()
        frames = session._frame_seq
        # 空闲不再 60Hz 全量渲染：首帧 + 可能的启动脏帧，远少于 60Hz 拍数。
        assert frames <= 3, f"空闲仍在持续全量渲染：{frames} 帧"
        assert frames >= 1
        assert len(calls) <= 4

    def test_request_wakes_render(self):
        calls: list = []
        session = _make_session(calls)
        session.start()
        try:
            time.sleep(0.1)
            before = session._frame_seq
            session._request_render()
            time.sleep(0.15)
        finally:
            session.stop()
        assert session._frame_seq > before, "请求后未渲染"
        assert len(calls) > 1

    def test_frame_rate_is_capped_at_60hz(self):
        calls: list = []
        session = _make_session(calls)
        session.start()
        try:
            start = time.monotonic()
            for _ in range(20):
                session._request_render()
                time.sleep(0.002)
            time.sleep(0.05)
            elapsed = max(0.05, time.monotonic() - start)
        finally:
            session.stop()
        # 连续请求不得超频：帧数 <= 60Hz * 时长 + 首帧余量。
        assert session._frame_seq <= int(elapsed * RENDER_HZ) + 3
