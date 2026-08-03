"""测试 ink react-ink 语义增强（完善 react ink，本轮新增）。

覆盖：
  - TEXT shorthand 样式 props（color/bold/italic/underline/dim/backgroundColor）
  - TEXT transform prop（uppercase/lowercase/capitalize）
  - BOX borderColor shorthand
  - 显式 style 与 shorthand 合并优先级
"""

from __future__ import annotations

from src.tui.ink import h, BOX, TEXT
from src.tui.ink.output import Frame
from src.tui.ink.reconciler import Reconciler
from src.tui.ink import components as _components


def _render(el, width: int = 30) -> Frame:
    root = Reconciler.create_root()
    recon = Reconciler()
    recon.render(root, el, width, 24)
    return _components.render_frame(root, width)


def _plains(frame: Frame) -> list[str]:
    return [line.plain for line in frame.lines]


class TestTextShorthandStyle:
    """TEXT shorthand 样式 props（react-ink 语义）。"""

    def test_color_and_bold(self):
        frame = _render(h(TEXT, {"children": "hi", "color": 45, "bold": True}))
        run = frame.lines[0].runs[0]
        assert run.style is not None
        assert run.style.fg == 45
        assert run.style.bold is True

    def test_background_color(self):
        frame = _render(h(TEXT, {"children": "x", "backgroundColor": 236}))
        assert frame.lines[0].runs[0].style.bg == 236

    def test_named_color(self):
        frame = _render(h(TEXT, {"children": "x", "color": "red"}))
        assert frame.lines[0].runs[0].style.fg == 1
        frame = _render(h(TEXT, {"children": "x", "color": "cyan"}))
        assert frame.lines[0].runs[0].style.fg == 6

    def test_style_and_shorthand_merge(self):
        from src.tui.core.style import Style
        frame = _render(h(TEXT, {"children": "x", "style": Style(fg=10), "color": 200}))
        assert frame.lines[0].runs[0].style.fg == 200  # shorthand 覆盖

    def test_no_style_when_none(self):
        frame = _render(h(TEXT, {"children": "x"}))
        assert frame.lines[0].runs[0].style is None


class TestTextTransform:
    """TEXT transform prop。"""

    def test_uppercase(self):
        frame = _render(h(TEXT, {"children": "hello world", "transform": "uppercase"}))
        assert _plains(frame)[0] == "HELLO WORLD"

    def test_lowercase(self):
        frame = _render(h(TEXT, {"children": "HELLO", "transform": "lowercase"}))
        assert _plains(frame)[0] == "hello"

    def test_capitalize(self):
        frame = _render(h(TEXT, {"children": "hello", "transform": "capitalize"}))
        assert _plains(frame)[0] == "Hello"

    def test_no_transform(self):
        frame = _render(h(TEXT, {"children": "Hello"}))
        assert _plains(frame)[0] == "Hello"


class TestBorderColor:
    """BOX borderColor shorthand。"""

    def test_border_color_int(self):
        frame = _render(h(BOX, {"width": 5, "border": 1, "borderColor": 45}, [
            h(TEXT, {"children": "x"}),
        ]))
        assert frame.lines[0].runs[0].style.fg == 45

    def test_border_color_named(self):
        frame = _render(h(BOX, {"width": 5, "border": 1, "borderColor": "red"}, [
            h(TEXT, {"children": "x"}),
        ]))
        assert frame.lines[0].runs[0].style.fg == 1

    def test_border_color_default(self):
        frame = _render(h(BOX, {"width": 5, "border": 1}, [
            h(TEXT, {"children": "x"}),
        ]))
        assert frame.lines[0].runs[0].style.fg == 23


class TestDisplayNone:
    """BOX display:none（react-ink 语义）。"""

    def test_hidden_box_skipped(self):
        frame = _render(h(BOX, None, [
            h(TEXT, {"children": "a"}),
            h(BOX, {"display": "none"}, [h(TEXT, {"children": "hidden"})]),
            h(TEXT, {"children": "b"}),
        ]))
        assert _plains(frame) == ["a", "b"]

    def test_hidden_text(self):
        frame = _render(h(BOX, None, [
            h(TEXT, {"children": "a", "display": "none"}),
            h(TEXT, {"children": "b"}),
        ]))
        assert _plains(frame) == ["b"]
