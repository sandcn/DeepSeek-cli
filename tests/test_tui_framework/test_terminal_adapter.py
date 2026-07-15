"""Tests for tui_framework.terminal.adapter module."""
import pytest
from tui_framework.terminal.adapter import TerminalAdapter, query_terminal_size


class TestQueryTerminalSize:
    """Tests for query_terminal_size."""

    def test_returns_tuple(self):
        """Should return (cols, rows) tuple."""
        result = query_terminal_size()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_positive_dimensions(self):
        """Both dimensions should be positive."""
        cols, rows = query_terminal_size()
        assert cols > 0
        assert rows > 0


class TestTerminalAdapter:
    """Tests for TerminalAdapter (framework version)."""

    def test_create_default(self):
        """Should create with default stdout."""
        adapter = TerminalAdapter()
        assert adapter is not None

    def test_terminal_width(self):
        """terminal_width should return positive int."""
        adapter = TerminalAdapter()
        w = adapter.terminal_width
        assert isinstance(w, int)
        assert w > 0

    def test_terminal_height(self):
        """terminal_height should return positive int."""
        adapter = TerminalAdapter()
        h = adapter.terminal_height
        assert isinstance(h, int)
        assert h > 0

    def test_get_terminal_size(self):
        """get_terminal_size should return (cols, rows)."""
        adapter = TerminalAdapter()
        cols, rows = adapter.get_terminal_size()
        assert cols > 0
        assert rows > 0

    def test_write_does_not_crash(self):
        """write should not raise."""
        import io
        adapter = TerminalAdapter(stdout=io.StringIO())
        adapter.write("test")  # should not raise

    def test_write_line_does_not_crash(self):
        """write_line should not raise."""
        import io
        adapter = TerminalAdapter(stdout=io.StringIO())
        adapter.write_line("test")  # should not raise

    def test_set_window_title(self):
        """set_window_title should not raise."""
        TerminalAdapter.set_window_title("Test Title")  # should not raise
