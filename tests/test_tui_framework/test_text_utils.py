"""Tests for tui_framework.core.text_utils module."""
import pytest
from tui_framework.core.text_utils import (
    truncate, build_gradient_ansi, build_gradient_ansi_frame,
    build_fade_in_ansi, make_sep_gradient,
)


class TestTruncate:
    """Tests for truncate function."""

    def test_short_text(self):
        """Short text should not be truncated."""
        assert truncate("hello", 10) == "hello"

    def test_long_text_truncated(self):
        """Long text should be truncated with suffix."""
        result = truncate("hello world", 5)
        assert len(result) <= 5 + 1  # +1 for ellipsis

    def test_none_text(self):
        """None should return empty string."""
        assert truncate(None, 10) == ""

    def test_empty_text(self):
        """Empty string should return empty string."""
        assert truncate("", 10) == ""

    def test_negative_max_len_raises(self):
        """Negative max_len should raise ValueError."""
        with pytest.raises(ValueError):
            truncate("hello", -1)

    def test_normalize_newlines(self):
        """Newlines should be normalized to spaces."""
        result = truncate("hello\nworld", 20)
        assert "\n" not in result


class TestBuildGradientAnsi:
    """Tests for build_gradient_ansi."""

    def test_single_color(self):
        """Single color should produce valid ANSI."""
        result = build_gradient_ansi([45])
        assert "\033[38;5;45m" in result
        assert "\033[0m" in result

    def test_multiple_colors(self):
        """Multiple colors should produce valid ANSI."""
        result = build_gradient_ansi([45, 46, 47])
        assert "\033[38;5;45m" in result
        assert "\033[38;5;46m" in result
        assert "\033[38;5;47m" in result

    def test_no_reset_suffix(self):
        """Without reset suffix should not include RESET."""
        result = build_gradient_ansi([45], suffix_reset=False)
        assert "\033[0m" not in result


class TestBuildGradientAnsiFrame:
    """Tests for build_gradient_ansi_frame."""

    def test_valid_index(self):
        """Valid index should return color ANSI."""
        result = build_gradient_ansi_frame([45, 46, 47], 0)
        assert "\033[38;5;45m" in result

    def test_index_wrap(self):
        """Index wrap should cycle."""
        result = build_gradient_ansi_frame([45, 46, 47], 5)
        # 5 % 3 = 2, so index 2 = 47
        assert "\033[38;5;47m" in result

    def test_empty_colors(self):
        """Empty colors should return empty string."""
        assert build_gradient_ansi_frame([], 0) == ""


class TestMakeSepGradient:
    """Tests for make_sep_gradient."""

    def test_basic_gradient(self):
        """Should produce valid ANSI gradient."""
        result = make_sep_gradient(5)
        assert "\033[0m" in result
        assert len(result) > 0

    def test_custom_colors(self):
        """Custom colors should be used."""
        result = make_sep_gradient(3, start_color=100, end_color=200)
        assert "\033[38;5;" in result

    def test_zero_width(self):
        """Zero width should produce empty gradient."""
        result = make_sep_gradient(0)
        assert result == "\033[0m"
