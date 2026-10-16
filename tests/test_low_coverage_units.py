"""低覆盖核心单元的行为测试 — 文件更新语义与 YAML 子集解析器。

覆盖此前覆盖率偏低的纯逻辑/核心路径：

- ``src/tools/update_file.py``（高频写入路径）——精确替换、正则替换、
  追加模式、歧义检测、正则标志解析、内容大小校验；
- ``src/kernel/_yaml.py``（无 PyYAML 时的清单兜底解析器）——嵌套映射、
  列表、行内列表/映射、注释、标量类型与错误分支。
"""

from __future__ import annotations

import pytest

from src.kernel import _yaml as ky
from src.tools.update_file import (
    AmbiguousMatchError,
    RegexCompileError,
    StringNotFoundError,
    UpdateFileFunc,
    _parse_regex_flags,
)


# ── _parse_regex_flags ────────────────────────────────────


def test_parse_regex_flags_empty():
    assert _parse_regex_flags("") == 0


def test_parse_regex_flags_combination():
    import re

    flags = _parse_regex_flags("im")
    assert flags & re.IGNORECASE
    assert flags & re.MULTILINE
    assert _parse_regex_flags("imsax") == (
        re.IGNORECASE | re.MULTILINE | re.DOTALL | re.ASCII | re.VERBOSE
    )


def test_parse_regex_flags_case_insensitive():
    assert _parse_regex_flags("I") == _parse_regex_flags("i")


def test_parse_regex_flags_unknown_raises():
    with pytest.raises(RegexCompileError):
        _parse_regex_flags("z")


# ── update_file 语义 ──────────────────────────────────────


def _func(tmp_path, content: str, old: str, new: str, **kwargs) -> UpdateFileFunc:
    path = tmp_path / "target.txt"
    path.write_text(content, encoding="utf-8")
    return UpdateFileFunc(str(path), old, new, **kwargs)


async def test_exact_unique_replacement(tmp_path):
    func = _func(tmp_path, "alpha beta gamma", "beta", "BETA")
    assert await func._get_new_content() == "alpha BETA gamma"


async def test_exact_string_not_found(tmp_path):
    func = _func(tmp_path, "alpha", "zzz", "y")
    with pytest.raises(StringNotFoundError) as exc:
        await func._get_new_content()
    assert "未找到匹配内容" in str(exc.value)


async def test_exact_ambiguous_without_replace_all(tmp_path):
    func = _func(tmp_path, "x x", "x", "y")
    with pytest.raises(AmbiguousMatchError) as exc:
        await func._get_new_content()
    assert "出现了2次" in str(exc.value)


async def test_exact_replace_all(tmp_path):
    func = _func(tmp_path, "x x x", "x", "y", replace_all=True)
    assert await func._get_new_content() == "y y y"


async def test_exact_deletion_with_empty_new_string(tmp_path):
    func = _func(tmp_path, "keep drop keep", "drop ", "")
    assert await func._get_new_content() == "keep keep"


async def test_append_mode_to_nonempty_file(tmp_path):
    func = _func(tmp_path, "line1\n", "", "line2")
    assert await func._get_new_content() == "line1\nline2"


async def test_append_mode_adds_newline_when_missing(tmp_path):
    func = _func(tmp_path, "line1", "", "line2")
    assert await func._get_new_content() == "line1\nline2"


async def test_append_mode_to_missing_file_creates_content(tmp_path):
    path = tmp_path / "new.txt"
    func = UpdateFileFunc(str(path), "", "fresh")
    assert await func._get_new_content() == "fresh"


async def test_empty_file_with_nonempty_old_string_raises(tmp_path):
    func = _func(tmp_path, "", "x", "y")
    with pytest.raises(StringNotFoundError) as exc:
        await func._get_new_content()
    assert "文件不存在或为空" in str(exc.value)


async def test_regex_replacement_single(tmp_path):
    func = _func(tmp_path, "abc123", r"\d+", "N", use_regex=True)
    assert await func._get_new_content() == "abcN"


async def test_regex_replacement_all(tmp_path):
    func = _func(tmp_path, "a1b2", r"\d", "#", use_regex=True, replace_all=True)
    assert await func._get_new_content() == "a#b#"


async def test_regex_backreference(tmp_path):
    func = _func(tmp_path, "key=value", r"(\w+)=(\w+)", r"\2=\1", use_regex=True)
    assert await func._get_new_content() == "value=key"


async def test_regex_no_match_raises(tmp_path):
    func = _func(tmp_path, "abc", r"\d+", "N", use_regex=True)
    with pytest.raises(StringNotFoundError):
        await func._get_new_content()


async def test_regex_ambiguous_without_replace_all(tmp_path):
    func = _func(tmp_path, "a1b2", r"\d", "#", use_regex=True)
    with pytest.raises(AmbiguousMatchError) as exc:
        await func._get_new_content()
    assert "匹配了2处" in str(exc.value)


async def test_regex_invalid_pattern_raises_regex_compile_error(tmp_path):
    func = _func(tmp_path, "abc", r"(unclosed", "x", use_regex=True)
    with pytest.raises(RegexCompileError):
        await func._get_new_content()


async def test_regex_flags_ignorecase(tmp_path):
    func = _func(tmp_path, "Hello", "hello", "hi", use_regex=True, regex_flags="i")
    assert await func._get_new_content() == "hi"


async def test_regex_flags_invalid_character_surfaces_as_compile_error(tmp_path):
    func = _func(tmp_path, "Hello", "hello", "hi", use_regex=True, regex_flags="z")
    with pytest.raises(RegexCompileError):
        await func._get_new_content()


def test_display_params_annotations(tmp_path):
    args = {"path": "a/b.py", "use_regex": True, "replace_all": True}
    text = UpdateFileFunc.display_params(args)
    assert "a/b.py" in text
    assert "[正则]" in text
    assert "[全局替换]" in text


def test_mode_desc_variants(tmp_path):
    plain = UpdateFileFunc("p", "a", "b")
    assert plain._mode_desc() == "字符串替换"
    assert UpdateFileFunc("p", "a", "b", replace_all=True)._mode_desc() == "字符串全局替换"
    assert UpdateFileFunc("p", "a", "b", use_regex=True)._mode_desc() == "正则替换"
    assert UpdateFileFunc("p", "", "b")._mode_desc() == "追加内容"


def test_success_verb():
    assert UpdateFileFunc("p", "a", "b")._success_verb() == "更新成功"


# ── YAML 子集解析器 ───────────────────────────────────────


def test_yaml_empty_text_returns_empty_dict():
    assert ky.load("") == {}
    assert ky.load("\n\n") == {}


def test_yaml_nested_map():
    text = "a: 1\nb:\n  c: 2\n  d: 3\n"
    assert ky.load(text) == {"a": 1, "b": {"c": 2, "d": 3}}


def test_yaml_list_of_scalars():
    assert ky.load("- 1\n- 2\n- three\n") == [1, 2, "three"]


def test_yaml_list_of_mappings():
    text = "- id: a\n  name: first\n- id: b\n  name: second\n"
    assert ky.load(text) == [
        {"id": "a", "name": "first"},
        {"id": "b", "name": "second"},
    ]


def test_yaml_inline_list_and_map():
    parsed = ky.load("a: [1, 2, 3]\nb: {x: 1, y: two}\n")
    assert parsed == {"a": [1, 2, 3], "b": {"x": 1, "y": "two"}}


def test_yaml_empty_inline_containers():
    assert ky.load("a: []\nb: {}\n") == {"a": [], "b": {}}


def test_yaml_nested_inline_deep():
    parsed = ky.load("a: [[1, 2], [3]]\n")
    assert parsed == {"a": [[1, 2], [3]]}


def test_yaml_scalar_types():
    parsed = ky.load(
        "t: true\nf: false\ny: yes\nn: no\nz: null\ntilde: ~\n"
        "i: -5\nfl: 1.25\ns: text\n"
    )
    assert parsed["t"] is True
    assert parsed["f"] is False
    assert parsed["y"] is True
    assert parsed["n"] is False
    assert parsed["z"] is None
    assert parsed["tilde"] is None
    assert parsed["i"] == -5
    assert parsed["fl"] == 1.25
    assert parsed["s"] == "text"


def test_yaml_quoted_strings_preserved():
    parsed = ky.load("a: '123'\nb: \"true\"\n")
    assert parsed["a"] == "123"
    assert parsed["b"] == "true"


def test_yaml_comments_stripped():
    parsed = ky.load("# leading\na: 1  # trailing\nb: '# not comment'\n")
    assert parsed == {"a": 1, "b": "# not comment"}


def test_yaml_key_without_value_is_none():
    assert ky.load("a:\n") == {"a": None}


def test_yaml_list_item_without_value_is_none():
    assert ky.load("-\n- x\n") == [None, "x"]


def test_yaml_tab_indent_rejected():
    with pytest.raises(ky.YamlError):
        ky.load("a:\n\tb: 1\n")


def test_yaml_unexpected_indent_rejected():
    with pytest.raises(ky.YamlError):
        ky.load("a: 1\n  b: 2\n")


def test_yaml_invalid_inline_mapping_entry():
    with pytest.raises(ky.YamlError):
        ky.load("a: {broken}\n")


def test_yaml_error_is_value_error():
    assert issubclass(ky.YamlError, ValueError)


def test_yaml_matches_pyyaml_when_available():
    """有 PyYAML 时两者对清单常见形态解析一致（防兜底实现漂移）。"""
    yaml = pytest.importorskip("yaml")
    text = "a: 1\nb:\n  - x\n  - y\nc: [1, 2]\nd:\n  e: true\n"
    assert ky.load(text) == yaml.safe_load(text)


def test_yaml_manifest_roundtrip_through_loader():
    """清单加载器可用内置子集解析器解析（不依赖 PyYAML）。"""
    from src.kernel.manifest import load_text

    data = load_text("- id: demo\n  plugin: pkg.mod\n")
    assert data == [{"id": "demo", "plugin": "pkg.mod"}]
