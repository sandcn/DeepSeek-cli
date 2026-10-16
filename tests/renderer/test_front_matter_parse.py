"""Front Matter 纯解析逻辑测试（``src.renderer._front_matter``）。

两渲染路径共享的解析真源：定界符判定、内容解析（YAML / TOML / JSON）、
`list` 续行、注释忽略、异常回退。
"""

from __future__ import annotations

from src.renderer._front_matter import (
    fm_split,
    fm_value_text,
    front_matter_delim,
    front_matter_format,
    is_front_matter_close,
    parse_front_matter_items,
)


def test_front_matter_delim_detection():
    assert front_matter_delim("---") == "---"
    assert front_matter_delim("+++") == "+++"
    assert front_matter_delim("{") == "{"
    assert front_matter_delim("--- ") is None
    assert front_matter_delim("正文") is None


def test_front_matter_format_mapping():
    assert front_matter_format("---") == "yaml"
    assert front_matter_format("+++") == "toml"
    assert front_matter_format("{") == "json"
    assert front_matter_format("?") == "yaml"


def test_is_front_matter_close_variants():
    assert is_front_matter_close("---", "---")
    assert is_front_matter_close("...", "---")
    assert not is_front_matter_close("+++", "---")
    assert is_front_matter_close("+++", "+++")
    assert is_front_matter_close("}", "{")


def test_fm_split_yaml_and_toml():
    assert fm_split("title: 值", "yaml") == ("title", "值")
    assert fm_split("title = 值", "toml") == ("title", "值")
    assert fm_split("没有分隔符", "yaml") == ("没有分隔符", "")


def test_fm_value_text_types():
    assert fm_value_text("s") == "s"
    assert fm_value_text(True) == "true"
    assert fm_value_text(None) == "null"
    assert fm_value_text(3) == "3"
    assert fm_value_text(["a", "b"]) == '["a", "b"]'


def test_parse_yaml_items_with_list_and_comment():
    text = "title: hello\n# 注释\ntags:\n  - a\n  - b"
    items = dict(parse_front_matter_items(text, "yaml"))
    assert items["title"] == "hello"
    assert "a" in items["tags"] and "b" in items["tags"]


def test_parse_toml_items():
    items = dict(parse_front_matter_items('title = "x"\ncount = 2', "toml"))
    assert items["title"] == '"x"'
    assert items["count"] == "2"


def test_parse_json_items():
    items = dict(parse_front_matter_items('{"a": 1, "b": "x"}', "json"))
    assert items["a"] == "1"
    assert items["b"] == "x"


def test_parse_json_invalid_returns_empty():
    assert parse_front_matter_items("{ not json", "json") == []


def test_parse_empty_text_returns_empty():
    assert parse_front_matter_items("", "yaml") == []
    assert parse_front_matter_items("\n\n", "yaml") == []
