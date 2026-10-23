"""帧率可配置 / 空闲不写终端 / 视图定时刷新（2026-10-23）回归测试。"""

from __future__ import annotations

import io

from src.tui._config import TuiConfig
from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _frame(text: str = "x") -> Frame:
    line = Line()
    line.append(text)
    return Frame([line])


def test_render_interval_is_configurable() -> None:
    config = TuiConfig.defaults().with_overrides(render_interval=0.05)
    assert config.render_interval == 0.05


def test_invalid_render_interval_falls_back_to_60hz() -> None:
    from src.tui._config import RENDER_INTERVAL_SEC

    for bad in (0, -1, float("nan"), float("inf")):
        config = TuiConfig.defaults().with_overrides(render_interval=bad)
        assert config.render_interval == RENDER_INTERVAL_SEC


def test_idle_cursor_skips_write_when_unchanged() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=10)
    frame = _frame()
    renderer.render(frame)
    renderer.place_cursor(1, 1)
    size = len(stream.getvalue())

    renderer.render(frame)  # 无变化帧：不写终端
    renderer.place_cursor(1, 1)  # 光标目标未变：跳过 write/flush
    assert len(stream.getvalue()) == size


def test_cursor_writes_again_after_frame_change() -> None:
    stream = io.StringIO()
    renderer = InkRenderer(stream=stream, height=10)
    renderer.render(_frame("a"))
    renderer.place_cursor(1, 1)
    renderer.render(_frame("b"))
    renderer.place_cursor(1, 1)
    assert "b" in stream.getvalue()


def test_trace_view_declares_refresh_interval() -> None:
    from src.tui.app.view_registry import fullscreen_views

    views = fullscreen_views()
    view = views.get("trace")
    if view is None:
        import pytest

        pytest.skip("trace 视图未注册")
    assert getattr(view, "_ink_idle_static", False) is True
    assert getattr(view, "_ink_refresh_interval", None) == 1.0


def test_view_refresh_due_uses_last_build_time() -> None:
    mixin = object.__new__(_SessionFrameMixin)

    class _Model:
        fullscreen = "trace"
        bottom_view = ""

    mixin._model = _Model()
    mixin._last_frame_built_at = 0.0
    assert mixin._view_refresh_due() is True
    import time

    mixin._last_frame_built_at = time.monotonic()
    assert mixin._view_refresh_due() is False
