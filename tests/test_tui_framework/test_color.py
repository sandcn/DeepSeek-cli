"""Tests for tui_framework.core.color module."""
import pytest
from tui_framework.core.color import (
    Color256, RGB, TrueColor, GradientDescriptor,
    hex_to_rgb, rgb_to_256, lerp_color,
    to_ansi_fg, to_ansi_bg, to_256,
)


class TestRGB:
    """Tests for RGB value object."""

    def test_create_valid(self):
        """Valid RGB should be created."""
        rgb = RGB(100, 150, 200)
        assert rgb.r == 100
        assert rgb.g == 150
        assert rgb.b == 200

    def test_invalid_r(self):
        """Invalid red should raise ValueError."""
        with pytest.raises(ValueError):
            RGB(300, 0, 0)

    def test_invalid_g(self):
        """Invalid green should raise ValueError."""
        with pytest.raises(ValueError):
            RGB(0, 300, 0)

    def test_invalid_b(self):
        """Invalid blue should raise ValueError."""
        with pytest.raises(ValueError):
            RGB(0, 0, 300)

    def test_brightness(self):
        """Brightness should be in [0, 1]."""
        assert 0.0 <= RGB(0, 0, 0).brightness <= 1.0
        assert 0.0 <= RGB(255, 255, 255).brightness <= 1.0

    def test_str(self):
        """String representation should be readable."""
        assert str(RGB(10, 20, 30)) == "RGB(10, 20, 30)"


class TestColor256:
    """Tests for Color256 value object."""

    def test_create_valid(self):
        """Valid color should be created."""
        c = Color256(45)
        assert c.value == 45

    def test_invalid_value(self):
        """Value > 255 should raise ValueError."""
        with pytest.raises(ValueError):
            Color256(300)

    def test_negative_value(self):
        """Negative value should raise ValueError."""
        with pytest.raises(ValueError):
            Color256(-1)

    def test_from_rgb(self):
        """from_rgb should create valid Color256."""
        c = Color256.from_rgb(255, 0, 0)
        assert 0 <= c.value <= 255

    def test_addition(self):
        """Addition should clamp to [0, 255]."""
        assert (Color256(250) + 10).value == 255

    def test_subtraction(self):
        """Subtraction should clamp to [0, 255]."""
        assert (Color256(5) - 10).value == 0

    def test_equality(self):
        """Same values should be equal."""
        assert Color256(45) == Color256(45)

    def test_hash(self):
        """Hash should work for dict keys."""
        d = {Color256(45): "cyan"}
        assert d[Color256(45)] == "cyan"

    def test_str_returns_ansi(self):
        """str() should return ANSI fg sequence."""
        assert "\033[38;5;45m" in str(Color256(45))

    def test_brightness(self):
        """Brightness should be in [0, 1]."""
        assert 0.0 <= Color256(45).brightness <= 1.0


class TestTrueColor:
    """Tests for TrueColor value object."""

    def test_create_valid(self):
        """Valid TrueColor should be created."""
        tc = TrueColor(100, 150, 200)
        assert tc.r == 100

    def test_to_ansi_fg(self):
        """to_ansi_fg should produce 24-bit ANSI."""
        tc = TrueColor(100, 150, 200)
        assert tc.to_ansi_fg() == "\033[38;2;100;150;200m"

    def test_to_ansi_bg(self):
        """to_ansi_bg should produce 24-bit ANSI."""
        tc = TrueColor(100, 150, 200)
        assert tc.to_ansi_bg() == "\033[48;2;100;150;200m"

    def test_to_256(self):
        """to_256 should return valid color number."""
        tc = TrueColor(255, 0, 0)
        result = tc.to_256()
        assert 0 <= result <= 255

    def test_from_hex(self):
        """from_hex should create TrueColor from hex."""
        tc = TrueColor.from_hex("#FF0000")
        assert tc.r == 255
        assert tc.g == 0
        assert tc.b == 0


class TestGradientDescriptor:
    """Tests for GradientDescriptor."""

    def test_create_valid(self):
        """Valid descriptor should be created."""
        gd = GradientDescriptor(0, 255, 8)
        assert gd.start_color == 0
        assert gd.end_color == 255
        assert gd.steps == 8

    def test_resolve(self):
        """resolve should produce valid color list."""
        gd = GradientDescriptor(0, 100, 5)
        colors = gd.resolve()
        assert len(colors) == 5
        assert colors[0] == 0
        assert colors[-1] == 100

    def test_invalid_steps(self):
        """Steps < 1 should raise ValueError."""
        with pytest.raises(ValueError):
            GradientDescriptor(0, 255, 0)

    def test_invalid_effect(self):
        """Invalid effect should raise ValueError."""
        with pytest.raises(ValueError):
            GradientDescriptor(0, 255, 8, effect="unknown")


class TestConversionFunctions:
    """Tests for conversion functions."""

    def test_hex_to_rgb(self):
        """hex_to_rgb should produce correct RGB."""
        rgb = hex_to_rgb("#FF8040")
        assert rgb.r == 255
        assert rgb.g == 128
        assert rgb.b == 64

    def test_rgb_to_256(self):
        """rgb_to_256 should return valid color."""
        result = rgb_to_256(255, 0, 0)
        assert 0 <= result <= 255

    def test_lerp_color(self):
        """lerp_color should interpolate between two colors."""
        a, b = 0, 100
        mid = lerp_color(a, b, 0.5)
        assert 0 <= mid <= 255

    def test_lerp_bounds(self):
        """lerp with t=0 should return a, t=1 should return b."""
        assert lerp_color(0, 100, 0.0) == 0
        assert lerp_color(0, 100, 1.0) == 100

    def test_to_ansi_fg_int(self):
        """to_ansi_fg with int should produce 256-color ANSI."""
        result = to_ansi_fg(45)
        assert "\033[38;5;45m" in result

    def test_to_ansi_fg_color256(self):
        """to_ansi_fg with Color256 should produce 256-color ANSI."""
        result = to_ansi_fg(Color256(45))
        assert "\033[38;5;45m" in result

    def test_to_256_color256(self):
        """to_256 with Color256 should return value."""
        assert to_256(Color256(45)) == 45

    def test_to_256_int(self):
        """to_256 with int should pass through."""
        assert to_256(45) == 45
