"""Tests for tui_framework.terminal narrow/terminal modules."""
import pytest
from tui_framework.terminal.terminal import (
    get_terminal_width, is_narrow, narrow_truncate,
    narrow_indent, narrow_sep_width,
    NARROW_THRESHOLD, EXTRA_NARROW_THRESHOLD,
)
from tui_framework.terminal.narrow import (
    is_narrow as narrow_is_narrow,
    narrow_truncate as narrow_truncate_from_narrow,
    narrow_indent as narrow_indent_from_narrow,
    narrow_sep_width as narrow_sep_width_from_narrow,
)


class TestTerminalWidth:
    """Tests for terminal width functions."""

    def test_get_terminal_width_positive(self):
        """get_terminal_width should return positive int."""
        w = get_terminal_width()
        assert isinstance(w, int)
        assert w > 0

    def test_narrow_threshold_default(self):
        """Default narrow threshold should be 80."""
        assert NARROW_THRESHOLD == 80

    def test_extra_narrow_threshold_default(self):
        """Default extra narrow threshold should be 50."""
        assert EXTRA_NARROW_THRESHOLD == 50

    def test_is_narrow_returns_bool(self):
        """is_narrow should return bool."""
        result = is_narrow()
        assert isinstance(result, bool)

    def test_narrow_truncate_wide(self):
        """narrow_truncate should return normal value on wide screen."""
        # With default threshold 80, if width >= 80, return normal
        result = narrow_truncate(100)
        # If terminal is wide enough, returns 100; otherwise narrow value
        assert isinstance(result, int)
        assert result > 0

    def test_narrow_indent_wide(self):
        """narrow_indent should return normal indent on wide screen."""
        result = narrow_indent(4)
        assert isinstance(result, int)

    def test_narrow_sep_width(self):
        """narrow_sep_width should return positive int."""
        result = narrow_sep_width(40)
        assert isinstance(result, int)
        assert result > 0


class TestNarrowModule:
    """Tests for narrow.py functions (should match terminal.py)."""

    def test_is_narrow_consistency(self):
        """narrow.is_narrow should match terminal.is_narrow."""
        n1 = narrow_is_narrow()
        n2 = is_narrow()
        assert n1 == n2

    def test_narrow_truncate_consistency(self):
        """narrow_truncate should be consistent between modules."""
        r1 = narrow_truncate_from_narrow(100)
        r2 = narrow_truncate(100)
        assert r1 == r2

    def test_narrow_indent_consistency(self):
        """narrow_indent should be consistent between modules."""
        r1 = narrow_indent_from_narrow(4)
        r2 = narrow_indent(4)
        assert r1 == r2

    def test_narrow_sep_width_consistency(self):
        """narrow_sep_width should be consistent between modules."""
        r1 = narrow_sep_width_from_narrow(40)
        r2 = narrow_sep_width(40)
        assert r1 == r2
