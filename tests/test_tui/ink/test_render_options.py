"""render() 官方 options 补齐 + useApp().exit(value) 语义测试。"""

from __future__ import annotations

import asyncio
import io
import time

import pytest

from src.tui.ink import h, TEXT, render, renderToString, useApp, useLayoutEffect, useIsScreenReaderEnabled
from src.tui.ink import hooks as H
from tests.test_tui.ink._harness import Harness


def test_render_returns_full_instance_api():
    buf = io.StringIO()
    ctrl = render(h(TEXT, {"children": "hi"}), stdout=buf, width=20)
    try:
        for key in ("waitUntilExit", "unmount", "cleanup", "rerender", "clear",
                    "waitUntilRenderFlush"):
            assert key in ctrl
    finally:
        ctrl["unmount"]()


def test_render_max_fps_does_not_change_fixed_30hz(monkeypatch):
    """maxFps 参数保留（官方 API 兼容）但不再改变帧率——渲染线程恒定 30Hz。"""
    from src.tui.ink.session import InkSession

    captured: list = []
    real_init = InkSession.__init__

    def _spy_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        captured.append(self)

    monkeypatch.setattr(InkSession, "__init__", _spy_init)
    buf = io.StringIO()
    ctrl = render(h(TEXT, {"children": "x"}), stdout=buf, width=20, maxFps=5)
    try:
        time.sleep(0.05)
    finally:
        ctrl["unmount"]()
    assert "x" in buf.getvalue()
    assert captured
    assert captured[-1]._config.render_interval == pytest.approx(1.0 / 30)


def test_render_kitty_keyboard_enable_and_disable():
    buf = io.StringIO()
    ctrl = render(
        h(TEXT, {"children": "k"}),
        stdout=buf,
        width=20,
        kittyKeyboard={"mode": "enabled", "flags": ["reportEventTypes"]},
    )
    time.sleep(0.2)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\x1b[>2u" in out
    assert "\x1b[<u" in out


def test_render_kitty_keyboard_disabled_writes_nothing():
    buf = io.StringIO()
    ctrl = render(h(TEXT, {"children": "k"}), stdout=buf, width=20, kittyKeyboard=False)
    time.sleep(0.2)
    ctrl["unmount"]()
    out = buf.getvalue()
    assert "\x1b[>1u" not in out
    assert "\x1b[<u" not in out


def test_render_on_render_called():
    buf = io.StringIO()
    metrics = []
    ctrl = render(h(TEXT, {"children": "m"}), stdout=buf, width=20, onRender=metrics.append)
    try:
        deadline = time.monotonic() + 2.0
        while not metrics and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        ctrl["unmount"]()
    assert metrics
    assert "width" in metrics[0] and "height" in metrics[0]
    assert metrics[0]["width"] == 20


def test_render_screen_reader_option():
    buf = io.StringIO()

    def Comp(props):
        return h(TEXT, {"children": "sr" if useIsScreenReaderEnabled() else "no"})

    H.set_screen_reader_enabled(False)
    ctrl = render(h(Comp, {}), stdout=buf, width=20, isScreenReaderEnabled=True)
    time.sleep(0.2)
    ctrl["unmount"]()
    assert "sr" in buf.getvalue()
    H.set_screen_reader_enabled(False)


async def test_wait_until_exit_resolves_with_value():
    buf = io.StringIO()

    def Comp(props):
        app = useApp()

        def _exit():
            app["exit"]("done")

        useLayoutEffect(_exit, ())
        return h(TEXT, {"children": "x"})

    ctrl = render(h(Comp, {}), stdout=buf, width=20)
    result = await asyncio.wait_for(ctrl["waitUntilExit"](), timeout=5.0)
    assert result == "done"


async def test_wait_until_exit_rejects_with_error():
    buf = io.StringIO()

    def Comp(props):
        app = useApp()

        def _exit():
            app["exit"](RuntimeError("boom"))

        useLayoutEffect(_exit, ())
        return h(TEXT, {"children": "x"})

    ctrl = render(h(Comp, {}), stdout=buf, width=20)
    with pytest.raises(RuntimeError, match="boom"):
        await asyncio.wait_for(ctrl["waitUntilExit"](), timeout=5.0)


def test_render_screen_reader_option_restored_on_unmount():
    """render(isScreenReaderEnabled=True) 退出后不跨会话泄漏（P0 多会话隔离）。"""

    def Comp(props):
        return h(TEXT, {"children": "sr" if useIsScreenReaderEnabled() else "no"})

    buf_on = io.StringIO()
    ctrl = render(h(Comp, {}), stdout=buf_on, width=20, isScreenReaderEnabled=True)
    time.sleep(0.2)
    ctrl["unmount"]()
    # 新会话（未开启屏幕阅读器）不得看到上一会话的开关
    buf_off = io.StringIO()
    ctrl2 = render(h(Comp, {}), stdout=buf_off, width=20)
    time.sleep(0.2)
    ctrl2["unmount"]()
    assert "sr" in buf_on.getvalue()
    assert "no" in buf_off.getvalue()


def test_use_app_exit_forwards_args():
    """useApp().exit/clear 转发到**本会话**的 app control（多会话隔离）。"""
    calls = []
    harness = Harness(20)
    harness.hook_context.app_control = {
        "exit": lambda *a: calls.append(a),
        "clear": lambda: calls.append("clear"),
    }
    captured = {}

    def Comp(props):
        captured["app"] = useApp()
        return h(TEXT, {"children": "x"})

    harness.render(h(Comp, {}))
    captured["app"]["exit"]("v")
    assert calls == [("v",)]
    captured["app"]["clear"]()
    assert calls[-1] == "clear"


def test_use_app_noop_without_control():
    H.set_app_control(None)
    captured = {}

    def Comp(props):
        captured["app"] = useApp()
        return h(TEXT, {"children": "x"})

    renderToString(h(Comp, {}))
    assert captured["app"]["exit"]() is None
    assert captured["app"]["clear"]() is None


def test_use_app_identity_stable():
    H.set_app_control({"exit": lambda *a: None, "clear": lambda: None})
    seen = []

    def Comp(props):
        seen.append(useApp())
        return h(TEXT, {"children": "x"})

    try:
        harness = Harness(20)
        harness.render(h(Comp, {}))
        harness.render(h(Comp, {}))
        assert seen[0] is seen[1]
    finally:
        H.set_app_control(None)
