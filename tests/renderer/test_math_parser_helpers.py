"""math_parser_helpers 辅助函数测试 — 覆盖 src/renderer/math_parser_helpers.py。

验证花括号组提取、上下标转换、运算符检测、TeX 原语查找、矩阵行分割。
"""

import pytest

from src.renderer.math_parser_helpers import (
    _all_chars_mapped,
    _convert_to_subscript,
    _convert_to_superscript,
    _extract_braced_group,
    _find_tex_primitive,
    _has_operator,
    _skip_group,
    _skip_spaces,
    re_split_rows,
)
from src.renderer.math_symbols import _SUBSCRIPT_MAP, _SUPERSCRIPT_MAP


# ── _extract_braced_group ─────────────────────────────────

def test_extract_simple_group():
    content, end = _extract_braced_group("{abc}", 0)
    assert content == "abc"
    assert end == 5


def test_extract_nested_group():
    content, end = _extract_braced_group("{a{b}c}", 0)
    assert content == "a{b}c"
    assert end == 7


def test_extract_empty_group():
    content, end = _extract_braced_group("{}", 0)
    assert content == ""
    assert end == 2


def test_extract_group_not_at_brace():
    content, end = _extract_braced_group("abc", 1)
    assert content == ""
    assert end == 1


def test_extract_unclosed_group_keeps_content():
    # ★ 未闭合时不应多截掉最后一个字符
    content, _ = _extract_braced_group("{abc", 0)
    assert content == "abc"


def test_extract_unclosed_nested_group():
    content, _ = _extract_braced_group("{a{b}", 0)
    assert content == "a{b}"


def test_extract_group_nesting_too_deep_raises():
    s = "{" * 200 + "x"
    with pytest.raises(ValueError):
        _extract_braced_group(s, 0)


# ── _skip_group / _skip_spaces ─────────────────────────────

def test_skip_group_returns_content_and_end():
    content, end = _skip_group("{xyz}", 0)
    assert content == "xyz"
    assert end == 5


def test_skip_spaces():
    assert _skip_spaces("  abc", 0, 5) == 2
    assert _skip_spaces("abc", 0, 3) == 0
    assert _skip_spaces("   ", 0, 3) == 3


# ── 上下标转换 ────────────────────────────────────────────

def test_convert_to_superscript_digits():
    assert _convert_to_superscript("123") == "\u00B9\u00B2\u00B3"


def test_convert_to_subscript_digits():
    assert _convert_to_subscript("12") == "\u2081\u2082"


def test_convert_to_superscript_unmapped_kept():
    # 无映射字符（如大写 F）保留原样
    assert "F" not in _SUPERSCRIPT_MAP
    assert _convert_to_superscript("F") == "F"


def test_convert_to_subscript_unmapped_kept():
    assert "F" not in _SUBSCRIPT_MAP
    assert _convert_to_subscript("F") == "F"


def test_all_chars_mapped():
    assert _all_chars_mapped("123", _SUPERSCRIPT_MAP) is True
    assert _all_chars_mapped("F", _SUPERSCRIPT_MAP) is False
    assert _all_chars_mapped("", _SUPERSCRIPT_MAP) is True


# ── _has_operator ─────────────────────────────────────────

def test_has_operator_simple():
    assert _has_operator("a+b") is True
    assert _has_operator("a-b") is True
    assert _has_operator("a=b") is True
    assert _has_operator("ab") is False


def test_has_operator_ignores_braced_group():
    # 运算符在花括号组内不计入
    assert _has_operator("a{+}b") is False


def test_has_operator_ignores_brackets():
    # [ ] 不是真正的分组符，深度不计入
    assert _has_operator("a[+]b") is True


# ── _find_tex_primitive ───────────────────────────────────

def test_find_tex_primitive_over():
    idx = _find_tex_primitive("a\\over b", "over")
    assert idx == 1


def test_find_tex_primitive_choose():
    idx = _find_tex_primitive("n\\choose k", "choose")
    assert idx == 1


def test_find_tex_primitive_skips_nested():
    # 嵌套组内的 \over 应被跳过
    idx = _find_tex_primitive("{a\\over b}\\over c", "over")
    assert idx == 10


def test_find_tex_primitive_not_found():
    assert _find_tex_primitive("abc", "over") is None


def test_find_tex_primitive_command_name_boundary():
    # "over" 后面紧跟字母则不是完整命令
    assert _find_tex_primitive("\\overflow", "over") is None


# ── re_split_rows ─────────────────────────────────────────

def test_re_split_rows_single():
    assert re_split_rows("a") == ["a"]


def test_re_split_rows_double_backslash():
    assert re_split_rows("a\\\\b") == ["a", "b"]


def test_re_split_rows_with_spacing_param():
    # \\[2pt] 可选参数被忽略
    assert re_split_rows("a\\\\[2pt]b") == ["a", "b"]


def test_re_split_rows_with_star():
    assert re_split_rows("a\\\\*b") == ["a", "b"]


def test_re_split_rows_ignores_backslash_in_group():
    # 花括号内的反斜杠不视为换行
    assert re_split_rows("{a\\\\b}c") == ["{a\\\\b}c"]


def test_re_split_rows_empty_lines_skipped():
    assert re_split_rows("a\\\\\\\\b") == ["a", "b"]
