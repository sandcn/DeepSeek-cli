"""MultiPanel — 多面板分屏布局控件测试。"""

from __future__ import annotations

from src.tui.ink import h, TEXT, MultiPanel
from src.tui._input_parser import KeyEvent
from tests.test_tui.ink._harness import Harness


def _panels():
    return [
        {"title": "A", "content": h(TEXT, {"children": "a1"})},
        {"title": "B", "content": h(TEXT, {"children": "b1"})},
    ]


def test_multipanel_renders_all_panels():
    text = Harness(40).render(h(MultiPanel, {"panels": _panels(), "width": 40}))
    assert "A" in text and "a1" in text
    assert "B" in text and "b1" in text


def test_multipanel_horizontal_single_line_rows():
    text = Harness(40).render(
        h(MultiPanel, {"panels": _panels(), "direction": "horizontal", "width": 40})
    )
    # 每行总显示宽度恒为 40（左右分栏不溢出）
    for row in text.split("\n"):
        assert len(row) == 40


def test_multipanel_vertical_direction():
    text = Harness(30).render(
        h(MultiPanel, {"panels": _panels(), "direction": "vertical", "width": 20})
    )
    rows = text.split("\n")
    assert any("A" in r for r in rows)
    assert any("B" in r for r in rows)


def test_multipanel_empty_panels():
    text = Harness(20).render(h(MultiPanel, {"panels": []}))
    assert text == ""


def test_multipanel_active_index_highlight_differs():
    h1 = Harness(40)
    t0 = h1.frame(h(MultiPanel, {"panels": _panels(), "width": 40, "activeIndex": 0})).to_ansi()
    h2 = Harness(40)
    t1 = h2.frame(h(MultiPanel, {"panels": _panels(), "width": 40, "activeIndex": 1})).to_ansi()
    assert t0 != t1


def test_multipanel_tab_switches_active():
    changes = []
    el = h(
        MultiPanel,
        {"panels": _panels(), "width": 40, "onActiveChange": changes.append},
    )
    harness = Harness(40)
    harness.render(el)
    router = harness.reconciler._input_router_cache[1]
    assert router(KeyEvent(kind="tab")) is True
    assert changes[-1] == 1
    # 重新渲染后取新 router（闭包捕获最新 activeIndex），Shift+Tab 回退
    harness.render(el)
    router2 = harness.reconciler._input_router_cache[1]
    assert router2(KeyEvent(kind="tab", modifier=2)) is True
    assert changes[-1] == 0


def test_multipanel_not_focusable_no_consume():
    el = h(MultiPanel, {"panels": _panels(), "width": 40, "focusable": False})
    harness = Harness(40)
    harness.render(el)
    router = harness.reconciler._input_router_cache[1] if harness.reconciler._input_router_cache else None
    if router is not None:
        assert router(KeyEvent(kind="tab")) is False


def test_multipanel_equal_size_false_uses_natural_size():
    el_eq = h(MultiPanel, {"panels": _panels(), "width": 40})
    el_nat = h(MultiPanel, {"panels": _panels(), "width": 40, "equalSize": False})
    a = Harness(40).frame(el_eq).to_ansi()
    b = Harness(40).frame(el_nat).to_ansi()
    assert a != b


def test_multipanel_pane_height_with_border():
    panels = [{"title": "A", "content": h(TEXT, {"children": "a1"}), "height": 3}]
    text = Harness(40).render(h(MultiPanel, {"panels": panels, "width": 20}))
    rows = text.split("\n")
    # 上边框 + 标题 + 内容 + 下边框（height=3 内容区含标题行）
    assert rows[0].startswith("┌")
    assert rows[-1].startswith("└")
