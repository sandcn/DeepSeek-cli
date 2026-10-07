"""HTML 属性解析与渲染辅助测试（2026-10 HTML 增强）。

覆盖 ``src/renderer/_html_attrs.py``（标签/属性解析单一真源）与
``src/renderer/ansi/_math_env.py`` 的列格式解析，以及 Rich 路径新增行内节点
（``<big>`` / ``<q>``）的内容保留。
"""

from __future__ import annotations

from src.renderer._html_attrs import (
    parse_tag_name, parse_attrs, parse_open_tag, attr, has_attr, align_of,
    style_value, language_of, number_of, bool_attr, RAW_TEXT_TAGS,
)
from src.renderer.ansi._math_env import parse_colspec, column_aligns


# ── 属性解析 ──────────────────────────────────────────────


def test_parse_tag_name():
    assert parse_tag_name('<div class="x">') == "div"
    assert parse_tag_name("</DIV>") == "div"
    assert parse_tag_name("普通文本") == ""


def test_parse_attrs_quoted_and_bare():
    attrs = parse_attrs('<a href="http://x" target=_blank rel>')
    assert attrs["href"] == "http://x"
    assert attrs["target"] == "_blank"
    assert "rel" in attrs and attrs["rel"] == ""


def test_parse_attrs_single_quotes_and_entities():
    attrs = parse_attrs("<img alt='a &amp; b' src=\"x.png\">")
    assert attrs["alt"] == "a & b"
    assert attrs["src"] == "x.png"


def test_parse_attrs_gt_inside_quotes():
    attrs = parse_attrs('<div title="a > b" align="center">')
    assert attrs["title"] == "a > b"
    assert attrs["align"] == "center"


def test_parse_open_tag():
    tag, attrs = parse_open_tag('<progress value="70" max="100">')
    assert tag == "progress"
    assert attrs["value"] == "70" and attrs["max"] == "100"


def test_attr_and_has_attr():
    attrs = {"checked": "", "value": "3"}
    assert has_attr(attrs, "checked")
    assert attr(attrs, "value") == "3"
    assert attr(attrs, "missing", "d") == "d"


def test_align_of_variants():
    assert align_of({"align": "center"}) == "center"
    assert align_of({"align": "RIGHT"}) == "right"
    assert align_of({"style": "color:red; text-align: center;"}) == "center"
    assert align_of({"style": "text-align:justify"}) == "justify"
    assert align_of({}) == ""


def test_style_value():
    assert style_value({"style": "width: 3em; color: red"}, "color") == "red"
    assert style_value({}, "color") == ""


def test_language_of_variants():
    assert language_of({"class": "language-python"}) == "python"
    assert language_of({"class": "hljs lang-js"}) == "js"
    assert language_of({"class": "brush: ruby"}) == "ruby"
    assert language_of({"lang": "go"}) == "go"
    assert language_of({"data-lang": "rs"}) == "rs"
    assert language_of({"class": "foo bar"}) == ""


def test_number_of_and_bool_attr():
    assert number_of({"value": "70"}, "value") == 70.0
    assert number_of({"value": "0.5"}, "value") == 0.5
    assert number_of({"value": "50%"}, "value") == 50.0
    assert number_of({}, "value") is None
    assert number_of({"value": "abc"}, "value") is None
    assert bool_attr({"checked": ""}, "checked")
    assert bool_attr({"checked": "checked"}, "checked")
    assert not bool_attr({"checked": "false"}, "checked")
    assert not bool_attr({}, "checked")


def test_raw_text_tags_contains_core_tags():
    for tag in ("script", "style", "template", "noscript", "canvas", "svg", "math"):
        assert tag in RAW_TEXT_TAGS


# ── 列格式解析（数学环境） ────────────────────────────────


def test_parse_colspec_aligns_and_bars():
    aligns, bars = parse_colspec("l|c|r")
    assert aligns == ["l", "c", "r"]
    assert bars == [False, True, True, False]


def test_parse_colspec_left_border():
    aligns, bars = parse_colspec("|c|c|")
    assert aligns == ["c", "c"]
    assert bars == [True, True, True]


def test_parse_colspec_default_on_empty():
    assert parse_colspec("") == (["l", "c", "r"], [False, False, False, False])


def test_parse_colspec_parameterized_column_types():
    # ``p{3cm}`` / ``>{...}`` 等带参数列类型不误判参数中的字母为列
    assert parse_colspec("p{3cm}c") == (["l", "c"], [False, False, False])
    assert parse_colspec(">{\\bfseries}l c") == (["l", "c"], [False, False, False])
    assert parse_colspec("l|p{2cm}") == (["l", "l"], [False, True, False])


def test_column_aligns_per_environment():
    assert column_aligns("cases", 2) == ["l", "l"]
    assert column_aligns("aligned", 2) == ["r", "l"]
    assert column_aligns("gather", 1) == ["c"]


# ── Rich 路径：新增行内节点内容保留 ───────────────────────


def test_rich_path_big_and_quoted_nodes():
    from src.renderer.inline_renderer import InlineRenderer

    r = InlineRenderer()
    assert r.render("<big>大</big>").plain == "大"
    assert r.render("<q>引用</q>").plain == "\u300c引用\u300d"


def test_rich_path_unknown_container_keeps_text():
    from src.renderer.inline_renderer import InlineRenderer

    r = InlineRenderer()
    assert r.render("<label>标签</label>").plain == "标签"
    assert r.render("<font color='red'>红</font>").plain == "红"
