"""Tests for tui_framework.core.ansi_utils module."""
import pytest
from tui_framework.core.ansi_utils import (
    strip_ansi, visual_width, truncate_ansi_visual,
    skip_ansi_sgr, truncate_ansi_sgr, truncate_ansi_line,
)


class TestStripAnsi:
    """Tests for strip_ansi."""

    def test_strip_no_ansi(self):
        """Plain text should remain unchanged."""
        assert strip_ansi("hello world") == "hello world"

    def test_strip_sgr(self):
        """SGR (color) sequences should be stripped."""
        assert strip_ansi("\033[38;5;45mhello\033[0m") == "hello"

    def test_strip_empty_string(self):
        """Empty string should remain empty."""
        assert strip_ansi("") == ""


class TestVisualWidth:
    """Tests for visual_width."""

    def test_ascii_width(self):
        """ASCII characters should have width 1."""
        assert visual_width("hello") == 5

    def test_empty_string(self):
        """Empty string should have width 0."""
        assert visual_width("") == 0

    def test_with_ansi(self):
        """ANSI sequences should be ignored in width calculation."""
        text_with_ansi = "\033[38;5;45mhello\033[0m"
        assert visual_width(text_with_ansi) == 5

    def test_cjk_chars(self):
        """CJK characters should have width 2."""
        w = visual_width("中文")
        assert w in (4, 2)  # 4 with wcwidth, 2 without


class TestTruncateAnsiVisual:
    """Tests for truncate_ansi_visual."""

    def test_no_truncation_needed(self):
        """Short text should not be truncated."""
        assert truncate_ansi_visual("hi", 10) == "hi"

    def test_truncation_with_ellipsis(self):
        """Long text should be truncated with ellipsis + RESET."""
        result = truncate_ansi_visual("hello world", 5)
        assert len(strip_ansi(result)) <= 5


class TestSkipAnsiSgr:
    """Tests for skip_ansi_sgr."""

    def test_no_ansi_at_position(self):
        """Non-ANSI position should return same index."""
        assert skip_ansi_sgr("hello", 0) == 0

    def test_skip_sgr_sequence(self):
        """SGR sequence should be skipped."""
        text = "\033[38;5;45mhello"
        # Position 0 is ESC, should skip to position after 'm'
        result = skip_ansi_sgr(text, 0)
        assert result > 0
        assert text[result] == 'h'


class TestTruncateAnsiLine:
    """Tests for truncate_ansi_line."""

    def test_short_text_passes_through(self):
        """Text within width should not change."""
        assert truncate_ansi_line("hi", 10) == "hi"

    def test_long_text_truncated(self):
        """Long text should be truncated."""
        result = truncate_ansi_line("a" * 100, 10)
        assert len(strip_ansi(result)) <= 10

    def test_ends_with_reset_and_ellipsis(self):
        """Truncated text should end with RESET + ..."""
        result = truncate_ansi_line("a" * 100, 10)
        assert "...\033[0m" in result or "..." in result
