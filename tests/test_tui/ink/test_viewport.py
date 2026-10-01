"""Viewport — 可滚动视口控件测试。"""

from __future__ import annotations

from src.tui.ink import h, Viewport
from src.tui._input_parser import KeyEvent
from tests.test_tui.ink._harness import Harness


def _lines(n=20):
    return [f"line{i}" for i in range(n)]


def test_viewport_shows_top_window():
    text = Harness(40).render(h(Viewport, {"lines": _lines(10), "height": 3, "width": 10}))
    rows = text.split("\n")
    assert rows[0].startswith("line0")
    assert rows[1].startswith("line1")
    assert rows[2].startswith("line2")


def test_viewport_offset_prop():
    text = Harness(40).render(
        h(Viewport, {"lines": _lines(10), "height": 2, "width": 10, "offset": 5})
    )
    rows = text.split("\n")
    assert rows[0].startswith("line5")
    assert rows[1].startswith("line6")


def test_viewport_scrollbar_column():
    text = Harness(40).render(
        h(Viewport, {"lines": _lines(20), "height": 4, "width": 10, "showScrollbar": True})
    )
    for row in text.split("\n"):
        assert len(row) == 10
        assert row[-1] in "█│"


def test_viewport_no_scrollbar():
    text = Harness(40).render(
        h(Viewport, {"lines": _lines(20), "height": 2, "width": 8, "showScrollbar": False})
    )
    for row in text.split("\n"):
        assert "█" not in row and "│" not in row


def test_viewport_keyboard_scrolls_via_router():
    """非受控视口经 input router 键盘滚动（内部 state 生效）。"""
    changes = []
    el = h(
        Viewport,
        {
            "lines": _lines(20),
            "height": 3,
            "width": 10,
            "showScrollbar": False,
            "onOffsetChange": changes.append,
        },
    )
    harness = Harness(40)
    harness.render(el)
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="arrow_down")) is True
    assert changes[-1] == 1
    assert harness.render(el).split("\n")[0].startswith("line1")


def test_viewport_offset_clamped():
    text = Harness(40).render(
        h(Viewport, {"lines": _lines(5), "height": 2, "width": 8, "offset": 99, "showScrollbar": False})
    )
    rows = text.split("\n")
    assert rows[0].startswith("line3")
    assert rows[1].startswith("line4")


def test_viewport_empty_lines():
    text = Harness(40).render(
        h(Viewport, {"lines": [], "height": 2, "width": 6, "showScrollbar": False})
    )
    assert text.split("\n") == ["      ", "      "]


def test_viewport_height_with_border_fits_content():
    """height = 可见内容行数；带边框时 BOX 总高自动叠加边框（内容不越框）。"""
    text = Harness(40).render(
        h(Viewport, {"lines": _lines(20), "height": 2, "width": 10, "border": 1,
                     "showScrollbar": False})
    )
    rows = text.split("\n")
    assert len(rows) == 4  # 上边框 + 2 内容行 + 下边框
    assert rows[0].startswith("┌")
    assert rows[-1].startswith("└")
    assert rows[1].startswith("│line0")
    assert rows[2].startswith("│line1")

