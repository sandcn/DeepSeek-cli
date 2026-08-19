"""内联渲染预处理测试 — 覆盖 src/renderer/_inline_preprocess.py 与 HTML 实体解码。

验证破折号/省略号、箭头、数学比较符号、HTML 实体、Emoji 短代码、分数等智能排版。
"""

import pytest

from src.renderer._inline_preprocess import (
    _has_inline_format,
    _preprocess_arrows,
    _preprocess_dashes,
    _preprocess_html_entities,
    _preprocess_math_symbols,
    _preprocess_text,
    _skip_html_comment,
)
from src.renderer._utils._html_entities import _HTML_ENTITIES, decode_html_entities
from src.renderer.emoji_map import EMOJI_MAP


# ── _has_inline_format ─────────────────────────────────────

def test_has_inline_format_detects_markers():
    assert _has_inline_format("*bold*") is True
    assert _has_inline_format("_x_") is True
    assert _has_inline_format("[link]") is True


def test_has_inline_format_plain_text_false():
    assert _has_inline_format("hello world") is False


# ── _preprocess_dashes ─────────────────────────────────────

def test_dashes_em_dash():
    assert _preprocess_dashes("a --- b") == "a — b"


def test_dashes_en_dash():
    assert _preprocess_dashes("a -- b") == "a – b"


def test_dashes_ellipsis():
    assert _preprocess_dashes("wait...") == "wait…"


def test_dashes_double_dash_technical_not_converted():
    # --verbose 技术文本（-- 后跟字母）不转换
    assert _preprocess_dashes("--verbose") == "--verbose"


def test_dashes_html_comment_protected():
    # HTML 注释体内的 -- 不受保护转换
    text = "<!-- a -- b -->"
    assert "--" in _preprocess_dashes(text)


# ── _preprocess_arrows ─────────────────────────────────────

@pytest.mark.parametrize("src,expected", [
    ("a -> b", "a → b"),
    ("a <- b", "a ← b"),
    ("a => b", "a ⇒ b"),
    ("a <-> b", "a ↔ b"),
    ("==>", "⟹"),
])
def test_arrows(src, expected):
    assert _preprocess_arrows(src) == expected


def test_arrows_code_like_not_converted():
    # 类似代码的 "x->y"（无空格、x/y 为字母）不转换
    assert _preprocess_arrows("x->y") == "x->y"


# ── _preprocess_math_symbols ───────────────────────────────

@pytest.mark.parametrize("src,expected", [
    ("a <= b", "a ≤ b"),
    ("a >= b", "a ≥ b"),
    ("a != b", "a ≠ b"),
    ("a ~= b", "a ≈ b"),
    ("x +- y", "x ± y"),
])
def test_math_symbols(src, expected):
    assert _preprocess_math_symbols(src) == expected


# ── _preprocess_html_entities ──────────────────────────────

@pytest.mark.parametrize("src,expected", [
    ("&amp;", "&"),
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("(c)", "©"),
    ("(r)", "®"),
    ("(tm)", "™"),
    ("1/2", "½"),
])
def test_html_entities(src, expected):
    assert _preprocess_html_entities(src) == expected


def test_html_entities_emoji_shortcode():
    name = ":smile:"
    assert _preprocess_html_entities(name) == EMOJI_MAP[name]


def test_html_entities_numeric_entity():
    assert _preprocess_html_entities("&#169;") == "©"


# ── _skip_html_comment ─────────────────────────────────────

def test_skip_html_comment_enters_comment():
    result: list[str] = []
    i, in_comment = _skip_html_comment("<!-- x", 0, 6, result, False)
    assert in_comment is True
    assert i == 4


def test_skip_html_comment_exits_comment():
    result: list[str] = []
    i, in_comment = _skip_html_comment("-->", 0, 3, result, True)
    assert in_comment is False
    assert i == 3


# ── _preprocess_text（组合） ───────────────────────────────

def test_preprocess_text_arrow_then_symbols():
    assert _preprocess_text("a -> b <= c") == "a → b ≤ c"


def test_preprocess_text_no_trigger_returns_unchanged():
    assert _preprocess_text("plain text without triggers") == "plain text without triggers"


def test_preprocess_text_empty():
    assert _preprocess_text("") == ""


# ── decode_html_entities（_utils/_html_entities.py） ───────

def test_decode_html_entities_named():
    assert decode_html_entities("&amp;") == "&"
    assert decode_html_entities("&lt;x&gt;") == "<x>"


def test_decode_html_entities_no_ampersand():
    assert decode_html_entities("hello") == "hello"


def test_decode_html_entities_table_contains_common():
    assert _HTML_ENTITIES["&amp;"] == "&"
    assert _HTML_ENTITIES["&nbsp;"] == "\u00A0"
