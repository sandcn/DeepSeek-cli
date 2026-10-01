"""measureElement — React Ink 等价物（x/y/width/height）。"""

from __future__ import annotations

from src.tui.ink import BOX, TEXT, h, measureElement, renderToString, use_ref, useLayoutEffect


def test_measure_element_none():
    assert measureElement(None) == {"x": 0, "y": 0, "width": 0, "height": 0}


def test_measure_element_reads_layout_box():
    class Box:
        x, y, w, h = 3, 4, 10, 2

    assert measureElement(Box()) == {"x": 3, "y": 4, "width": 10, "height": 2}


def test_measure_element_accepts_ref_object():
    class Ref:
        def __init__(self, current):
            self.current = current

    class Box:
        x, y, w, h = 1, 2, 3, 4

    assert measureElement(Ref(Box())) == {"x": 1, "y": 2, "width": 3, "height": 4}


def test_measure_element_malformed_sizes():
    class Box:
        x, y, w, h = float("inf"), None, "bad", 5

    assert measureElement(Box()) == {"x": 0, "y": 0, "width": 0, "height": 0}


def test_measure_element_in_component_layout_effect():
    captured = {}

    def Comp(props):
        ref = use_ref(None)

        def _measure():
            captured.update(measureElement(ref))

        useLayoutEffect(_measure, None)
        return h(BOX, {"ref": ref, "width": 6, "height": 2}, h(TEXT, {"children": "abc"}))

    renderToString(h(Comp, {}))
    assert captured["width"] == 6
    assert captured["height"] == 2
    assert captured["x"] == 0 and captured["y"] == 0
