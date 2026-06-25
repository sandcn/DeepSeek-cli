"""测试 _text_utils 纯函数。

覆盖：
  - truncate() 基本截断 + normalize=True（默认）
  - truncate() normalize=False 路径
  - 自定义 suffix
  - 边界：max_len=0, 负数, 空字符串, None, 纯空白
"""

from __future__ import annotations

import pytest
from src.ui.common.text_utils import truncate


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
