"""内联渲染器测试 — 覆盖 src/renderer/inline_renderer.py。

验证 InlineRenderer.render 与 render_inline 便捷函数的内联 Markdown → Rich Text 转换。
"""

from rich.text import Text

import pytest

from src.renderer.inline_renderer import InlineRenderer, render_inline
from src.renderer.emoji_map import EMOJI_MAP


@pytest.fixture
def renderer():
    return InlineRenderer()


def test_render_plain_text(renderer):
    t = renderer.render("hello")
    assert isinstance(t, Text)
    assert t.plain == "hello"


def test_render_empty_returns_empty_text(renderer):
    t = renderer.render("")
    assert t.plain == ""


def test_render_bold(renderer):
    t = renderer.render("**bold**")
    assert t.plain == "bold"


def test_render_italic(renderer):
    t = renderer.render("*italic*")
    assert t.plain == "italic"


def test_render_inline_code(renderer):
    t = renderer.render("`code`")
    assert t.plain.strip() == "code"


def test_render_link(renderer):
    t = renderer.render("[text](http://example.com)")
    assert t.plain == "text"


def test_render_strikethrough(renderer):
    t = renderer.render("~~gone~~")
    assert t.plain == "gone"


def test_render_emoji_shortcode(renderer):
    t = renderer.render(":smile:")
    assert t.plain == EMOJI_MAP[":smile:"]


def test_render_html_entity(renderer):
    t = renderer.render("&amp;")
    assert t.plain == "&"


def test_render_function_convenience():
    t = render_inline("hi")
    assert t.plain == "hi"


def test_render_function_with_bold():
    t = render_inline("**x**")
    assert t.plain == "x"
