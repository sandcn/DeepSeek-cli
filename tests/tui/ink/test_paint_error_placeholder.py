"""paint 异常可见占位（2026-10-23 异常可观测性回归测试）。"""

from __future__ import annotations

from src.tui.ink import h, register_host, renderToString, unregister_host
from src.tui.ink.helpers import strip_ansi

_TAG = "boom-test-host"


def _measure(fiber, avail_w):
    return (avail_w, 1)


def _paint(fiber, canvas):
    raise RuntimeError("boom")


def test_paint_error_draws_visible_placeholder() -> None:
    register_host(_TAG, _measure, _paint)
    try:
        text = strip_ansi(renderToString(h(_TAG, None), {"columns": 40}))
    finally:
        unregister_host(_TAG)
    assert "render error" in text
    assert "RuntimeError" in text


def test_paint_error_is_recorded_on_fiber() -> None:
    from src.tui.ink.components import render_frame
    from src.tui.ink.reconciler import Reconciler

    register_host(_TAG, _measure, _paint)
    try:
        reconciler = Reconciler(None)
        root = reconciler.create_root()
        reconciler.render(root, h(_TAG, None), 20, 10)
        render_frame(root, 20)
        found = []
        stack = [root]
        while stack:
            fiber = stack.pop()
            if getattr(fiber, "_paint_error", None):
                found.append(fiber._paint_error)
            child = fiber.child
            while child is not None:
                stack.append(child)
                child = child.sibling
        assert found and "RuntimeError" in found[0]
    finally:
        unregister_host(_TAG)
