"""MathRenderer 门面测试 — 覆盖 src/renderer/math_renderer.py。

验证 render / render_inline / render_block 接口与异常降级行为。
"""

import pytest
from rich.panel import Panel
from rich.text import Text

from src.renderer.math_renderer import MathRenderer
from src.renderer.math_parser import MathParser


@pytest.fixture
def renderer():
    return MathRenderer()


def test_render_basic(renderer):
    t = renderer.render("x+1")
    assert isinstance(t, Text)
    assert "x" in t.plain


def test_render_inline_returns_text(renderer):
    t = renderer.render_inline("\\alpha")
    assert isinstance(t, Text)
    assert t.plain == "α"


def test_render_block_returns_panel(renderer):
    p = renderer.render_block("x")
    assert isinstance(p, Panel)


def test_render_block_panel_contains_expr(renderer):
    p = renderer.render_block("x^2")
    assert "x" in p.renderable.plain


def test_render_empty_expr(renderer):
    t = renderer.render("")
    assert isinstance(t, Text)


def test_render_parser_cached(renderer):
    renderer.render("x")
    assert isinstance(renderer._get_parser(), MathParser)
    assert renderer._parser is not None


def test_render_fallback_on_parse_error(renderer, monkeypatch):
    def boom(self, expr):
        raise ValueError("parse fail")

    monkeypatch.setattr(MathParser, "parse", boom)
    t = renderer.render("x+1")
    assert isinstance(t, Text)
    assert "⚠️" in t.plain
    assert "x+1" in t.plain


def test_render_fallback_empty_on_error(renderer, monkeypatch):
    def boom(self, expr):
        raise ValueError("parse fail")

    monkeypatch.setattr(MathParser, "parse", boom)
    t = renderer.render("")
    assert t.plain == ""
