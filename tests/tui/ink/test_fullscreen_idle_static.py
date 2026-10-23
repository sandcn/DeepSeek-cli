"""全屏视图「空闲静态」声明（2026-10-23 全屏视图脏驱动）回归测试。"""

from __future__ import annotations

import pytest

from src.tui._assembly_steps import _view_allows_idle_static
from src.tui.app.view_registry import fullscreen_views


def _views() -> dict:
    views = fullscreen_views()
    if not views:
        pytest.skip("视图注册表为空（清单接管/未初始化）")
    return views


def test_help_view_is_declared_idle_static() -> None:
    views = _views()
    view = views.get("help")
    if view is None:
        pytest.skip("help 视图未注册")
    assert getattr(view, "_ink_idle_static", False) is True


def test_trace_view_is_idle_static_with_low_frequency_refresh() -> None:
    """TraceView 含实时耗时：允许空闲复用，但每秒强制重建一帧。"""
    views = _views()
    view = views.get("trace")
    if view is None:
        pytest.skip("trace 视图未注册")
    assert getattr(view, "_ink_idle_static", False) is True
    assert getattr(view, "_ink_refresh_interval", None) == 1.0


def test_predicate_allows_declared_static_fullscreen_view() -> None:
    views = _views()
    if "help" not in views:
        pytest.skip("help 视图未注册")

    class _Model:
        fullscreen = "help"
        bottom_view = ""

    assert _view_allows_idle_static(_Model()) is True


def test_predicate_rejects_unknown_view() -> None:
    class _Model:
        fullscreen = "does-not-exist"
        bottom_view = ""

    assert _view_allows_idle_static(_Model()) is False
