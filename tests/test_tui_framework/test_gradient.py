"""Tests for tui_framework.core.gradient module."""
import pytest
from tui_framework.core.gradient import (
    _XTERM_PALETTE, _build_xterm_palette,
    hex_to_256, gradient_step, gradient_range,
)


class TestXtermPalette:
    """Tests for xterm-256 palette building."""

    def test_palette_length(self):
        """Palette should have exactly 256 entries."""
        assert len(_XTERM_PALETTE) == 256

    def test_palette_first_is_black(self):
        """Index 0 should be black (0,0,0)."""
        assert _XTERM_PALETTE[0] == (0, 0, 0)

    def test_palette_last_is_white(self):
        """Index 255 should be near white."""
        assert _XTERM_PALETTE[255] == (238, 238, 238)

    def test_rebuild_is_consistent(self):
        """Rebuilding palette should produce identical result."""
        assert _build_xterm_palette() == list(_XTERM_PALETTE)


class TestHexTo256:
    """Tests for hex_to_256 conversion."""

    def test_valid_hex_with_hash(self):
        """Hex with # prefix should convert."""
        result = hex_to_256("#FF0000")
        assert 0 <= result <= 255  # valid color, exact depends on palette

    def test_valid_hex_without_hash(self):
        """Hex without # prefix should convert."""
        result = hex_to_256("00FF00")
        assert 0 <= result <= 255

    def test_black_hex(self):
        """Black (#000000) should map to 0 or 16."""
        result = hex_to_256("#000000")
        assert result in (0, 16)

    def test_invalid_input_returns_15(self):
        """Invalid hex should return 15 (white) as fallback."""
        assert hex_to_256("not-a-color") == 15

    def test_short_hex(self):
        """Short hex should return 15."""
        assert hex_to_256("#FFF") == 15


class TestGradientStep:
    """Tests for gradient_step function."""

    def test_single_step(self):
        """Single step should return start color."""
        assert gradient_step(0, 255, 1, 0) == 0

    def test_first_step(self):
        """First step should equal start."""
        assert gradient_step(0, 100, 10, 0) == 0

    def test_last_step(self):
        """Last step should equal end."""
        assert gradient_step(0, 100, 10, 9) == 100

    def test_middle_step(self):
        """Middle step should be interpolated."""
        mid = gradient_step(0, 100, 3, 1)
        assert 0 <= mid <= 100

    def test_clamp_index(self):
        """Index beyond range should be clamped."""
        assert gradient_step(0, 200, 5, 100) == 200

    def test_negative_index(self):
        """Negative index should be clamped to 0."""
        assert gradient_step(0, 200, 5, -5) == 0


class TestGradientRange:
    """Tests for gradient_range function."""

    def test_empty_range(self):
        """Zero steps should return empty list."""
        assert gradient_range(0, 100, 0) == []

    def test_single_step(self):
        """Single step should return [start]."""
        assert gradient_range(42, 100, 1) == [42]

    def test_two_steps(self):
        """Two steps should return [start, end]."""
        assert gradient_range(0, 255, 2) == [0, 255]

    def test_length(self):
        """Result should have exactly steps entries."""
        for steps in [3, 5, 8, 10]:
            assert len(gradient_range(0, 255, steps)) == steps

    def test_monotonic(self):
        """Result should be monotonic for increasing gradient."""
        result = gradient_range(0, 100, 10)
        for i in range(len(result) - 1):
            assert result[i] <= result[i + 1]

    def test_all_in_range(self):
        """All values should be in [0, 255]."""
        result = gradient_range(50, 200, 20)
        for c in result:
            assert 0 <= c <= 255
