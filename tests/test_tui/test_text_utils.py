"""测试 _text_utils 纯函数。

覆盖：
  - truncate() 基本截断 + normalize=True（默认）
  - truncate() normalize=False 路径
  - 自定义 suffix
  - 边界：max_len=0, 负数, 空字符串, None, 纯空白
"""

from __future__ import annotations

import pytest
from src.ui.tui._text_utils import truncate, make_sep_gradient


class TestTruncateNormalizeTrue:
    """truncate() 默认 normalize=True 行为测试。"""

    def test_short_text_not_truncated(self):
        assert truncate("hello", 20) == "hello"

    def test_long_text_truncated(self):
        text = "a" * 100
        result = truncate(text, 10)
        assert len(result) == 11  # 10 + "…"
        assert result.endswith("…")

    def test_newlines_replaced(self):
        result = truncate("hello\nworld", 60)
        assert "\n" not in result
        assert "hello world" in result

    def test_none_input(self):
        assert truncate(None, 20) == ""

    def test_empty_string(self):
        assert truncate("", 20) == ""

    def test_boundary_exact_width(self):
        text = "a" * 20
        result = truncate(text, 20)
        assert result == text
        assert "…" not in result

    def test_boundary_one_over(self):
        text = "a" * 21
        result = truncate(text, 20)
        assert len(result) == 21  # 20 + "…"
        assert result.endswith("…")

    def test_whitespace_only_after_normalize(self):
        """纯空白字符串经 normalize 后变空串，应返回空字符串。"""
        assert truncate("   \n  ", 10) == ""

    def test_strip_applied(self):
        """normalize=True 去首尾空白。"""
        assert truncate("  hello  ", 20) == "hello"


class TestTruncateNormalizeFalse:
    """truncate() normalize=False 路径测试。"""

    def test_newlines_preserved(self):
        result = truncate("hello\nworld", 60, normalize=False)
        assert "\n" in result
        assert result == "hello\nworld"

    def test_whitespace_preserved(self):
        assert truncate("  hello  ", 20, normalize=False) == "  hello  "

    def test_long_text_with_newlines(self):
        text = "line1\nline2\n" + "a" * 100
        result = truncate(text, 15, normalize=False)
        assert len(result) == 16  # 15 + "…"
        assert "\n" in result


class TestTruncateCustomSuffix:
    """自定义 suffix 参数测试。"""

    def test_custom_suffix(self):
        result = truncate("hello world!", 5, suffix="...")
        assert result == "hello..."

    def test_empty_suffix(self):
        result = truncate("hello world!", 5, suffix="")
        assert result == "hello"

    def test_keyword_max_len(self):
        """直接以关键字形式传入 max_len。"""
        result = truncate("hello world", max_len=5)
        assert result == "hello…"


class TestTruncateMaxLenZero:
    """max_len=0 边界。"""

    def test_max_len_zero_with_nonempty(self):
        result = truncate("hello", 0)
        # 0 chars + "…" = 1 char
        assert result == "…"

    def test_max_len_zero_with_empty(self):
        assert truncate("", 0) == ""


class TestTruncateNegativeMaxLen:
    """max_len < 0 应抛出 ValueError。"""

    def test_negative_max_len_raises(self):
        with pytest.raises(ValueError, match="max_len must be >= 0"):
            truncate("hello", -1)

    def test_negative_max_len_with_none_returns_empty(self):
        """None 在 max_len 校验之前短路，始终返回 ''。"""
        assert truncate(None, -5) == ""


class TestMakeSepGradient:
    """make_sep_gradient() 统一渐变分隔线工厂测试。"""

    def test_make_sep_gradient_basic(self):
        """基础渐变分隔线输出：宽度>0，含 ANSI 色号和 RESET。"""
        result = make_sep_gradient(10)
        assert result.endswith("\033[0m"), "应以 RESET 结尾"
        assert "\033[38;5;" in result, "应含 ANSI 256 色序列"
        # 10 个渐变色，每个以 ANSI 序列开头
        ansi_count = result.count("\033[38;5;")
        assert ansi_count == 10, f"应有 10 个色段，实际 {ansi_count}"

    def test_make_sep_gradient_breath(self):
        """呼吸起始色：传入亮青(81)应输出更亮的渐变。"""
        result_bright = make_sep_gradient(10, start_color=81)
        result_default = make_sep_gradient(10, start_color=45)
        assert result_bright != result_default, "不同起始色应输出不同结果"
        assert result_bright.endswith("\033[0m")

    def test_make_sep_gradient_zero_width(self):
        """边界 width<=0 应返回空字符串或仅 RESET。"""
        result_zero = make_sep_gradient(0)
        assert result_zero == "\033[0m" or result_zero == "", \
            f"width=0 应返回空或仅 RESET, 实际: {repr(result_zero)}"
        result_neg = make_sep_gradient(-1)
        assert result_neg == "\033[0m" or result_neg == "", \
            f"width=-1 应返回空或仅 RESET, 实际: {repr(result_neg)}"

    def test_make_sep_gradient_custom_char(self):
        """自定义字符参数生效。"""
        result = make_sep_gradient(5, char="=")
        # ANSI 序列数量仍为 5
        ansi_count = result.count("\033[38;5;")
        assert ansi_count == 5, f"应有 5 个色段，实际 {ansi_count}"
        # 字符 = 应在输出中
        eq_count = result.count("=")
        assert eq_count == 5, f"应有 5 个 =，实际 {eq_count}"

    def test_make_sep_gradient_custom_end_color(self):
        """自定义结束色参数生效。"""
        result_dark = make_sep_gradient(10, start_color=45, end_color=237)
        result_light = make_sep_gradient(10, start_color=45, end_color=240)
        # 不同结束色应产生不同结果（并非严格不等，但大概率不同）
        assert result_dark != result_light, "不同结束色应输出不同结果"
