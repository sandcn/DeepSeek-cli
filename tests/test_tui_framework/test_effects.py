"""Tests for tui_framework.core.effects module."""
import pytest
from tui_framework.core.effects import (
    sine_breath_t, sine_color, sine_color_range,
    bounce_easing, bounce_frame_color,
    wave_offset, apply_wave,
    sparkle_brightness, sparkle_color,
    shimmer_position, shimmer_apply,
    build_glow_ansi, build_fg_breath_ansi, build_bg_breath_ansi,
    rainbow_color, pulse_train,
    neon_color, typewriter_cursor,
    sine_easing, EffectRegistry,
)


class TestSineBreath:
    """Tests for sine wave breathing functions."""

    def test_sine_breath_t_range(self):
        """sine_breath_t should return values in [0, 1]."""
        for frame in range(100):
            t = sine_breath_t(frame)
            assert 0.0 <= t <= 1.0

    def test_sine_breath_t_periodic(self):
        """sine_breath_t should be periodic."""
        t0 = sine_breath_t(0)
        t12 = sine_breath_t(12)
        assert abs(t0 - t12) < 1e-10

    def test_sine_color_range(self):
        """sine_color should return values in [low, high]."""
        for frame in range(100):
            c = sine_color(frame, 0, 100)
            assert 0 <= c <= 100

    def test_sine_color_range_list(self):
        """sine_color_range should interpolate within list."""
        colors = [0, 50, 100]
        result = sine_color_range(0, colors)
        assert 0 <= result <= 100

    def test_sine_color_range_empty(self):
        """Empty list should return fallback 45."""
        assert sine_color_range(0, []) == 45

    def test_sine_color_range_single(self):
        """Single color should return that color."""
        assert sine_color_range(0, [42]) == 42


class TestBounceEasing:
    """Tests for bounce easing."""

    def test_bounds(self):
        """bounce_easing should be bounded."""
        assert bounce_easing(0.0) == 0.0
        assert bounce_easing(1.0) == 1.0

    def test_monotonic_mostly(self):
        """bounce_easing should generally increase."""
        t0 = bounce_easing(0.2)
        t1 = bounce_easing(0.8)
        assert t0 <= 1.5  # can overshoot
        assert t1 <= 1.1

    def test_bounce_frame_color(self):
        """bounce_frame_color should return valid colors."""
        c = bounce_frame_color(0, 8)
        assert 0 <= c <= 255

    def test_bounce_frame_exceeds_total(self):
        """Frame beyond total should return 255."""
        assert bounce_frame_color(100, 8) == 255


class TestWaveEffect:
    """Tests for wave effects."""

    def test_wave_offset_returns_float(self):
        """wave_offset should return a float."""
        result = wave_offset(0, 0)
        assert isinstance(result, float)

    def test_apply_wave_preserves_length(self):
        """apply_wave should preserve list length."""
        colors = [40, 41, 42, 43, 44, 45]
        result = apply_wave(colors, 0)
        assert len(result) == len(colors)

    def test_apply_wave_colors_in_range(self):
        """apply_wave should keep colors in valid range."""
        colors = [100] * 10
        result = apply_wave(colors, 5, amplitude=5.0)
        for c in result:
            assert 0 <= c <= 255


class TestSparkleEffect:
    """Tests for sparkle effects."""

    def test_sparkle_brightness_range(self):
        """sparkle_brightness should return [0, 1]."""
        for frame in range(20):
            b = sparkle_brightness(frame)
            assert 0.0 <= b <= 1.0

    def test_sparkle_color_range(self):
        """sparkle_color should return valid colors."""
        for frame in range(20):
            c = sparkle_color(frame)
            assert 0 <= c <= 255


class TestShimmerEffect:
    """Tests for shimmer effects."""

    def test_shimmer_position_range(self):
        """shimmer_position should be in [0, total_width)."""
        for frame in range(20):
            pos = shimmer_position(frame, 10)
            assert 0.0 <= pos < 10.0

    def test_shimmer_apply_preserves_length(self):
        """shimmer_apply should preserve list length."""
        colors = list(range(20))
        result = shimmer_apply(colors, 0)
        assert len(result) == len(colors)


class TestAnsiBuilders:
    """Tests for ANSI builder functions."""

    def test_build_glow_ansi(self):
        """build_glow_ansi should produce valid ANSI."""
        result = build_glow_ansi(0)
        assert "\033[38;5;" in result

    def test_build_fg_breath_ansi(self):
        """build_fg_breath_ansi should produce valid ANSI."""
        result = build_fg_breath_ansi(0, 0, 100)
        assert "\033[38;5;" in result

    def test_build_bg_breath_ansi(self):
        """build_bg_breath_ansi should produce valid ANSI."""
        result = build_bg_breath_ansi(0, 0, 100)
        assert "\033[48;5;" in result


class TestNewEffects:
    """Tests for new rendering effects."""

    def test_rainbow_color(self):
        """rainbow_color should return valid colors."""
        for frame in range(20):
            c = rainbow_color(frame, 5)
            assert 0 <= c <= 255

    def test_pulse_train(self):
        """pulse_train should return valid colors."""
        for pos in range(10):
            c = pulse_train(0, pos, 10)
            assert 0 <= c <= 255

    def test_neon_color(self):
        """neon_color should return valid colors."""
        for frame in range(20):
            c = neon_color(frame)
            assert 0 <= c <= 255

    def test_typewriter_cursor(self):
        """typewriter_cursor should return cursor or space."""
        cursor = typewriter_cursor(0)
        assert cursor in ("\u258c", " ")

    def test_sine_easing(self):
        """sine_easing should return values in [0, 1]."""
        for t_val in [0.0, 0.25, 0.5, 0.75, 1.0]:
            result = sine_easing(t_val)
            assert 0.0 <= result <= 1.0

    def test_sine_easing_bounds(self):
        """sine_easing at bounds should be 0 and 1."""
        assert abs(sine_easing(0.0)) < 1e-10
        assert abs(sine_easing(1.0) - 1.0) < 1e-10


class TestEffectRegistry:
    """Tests for EffectRegistry."""

    def test_has_pre_registered(self):
        """Pre-registered effects should be available."""
        assert EffectRegistry.has("rainbow")
        assert EffectRegistry.has("aurora")
        assert EffectRegistry.has("pulse")
        assert EffectRegistry.has("wave")
        assert EffectRegistry.has("shimmer")
        assert EffectRegistry.has("heat_wave")
        assert EffectRegistry.has("sparkle")
        assert EffectRegistry.has("glow")

    def test_get_registered(self):
        """get should return callable for registered effect."""
        fn = EffectRegistry.get("rainbow")
        assert fn is not None
        result = fn(0, length=4)
        assert len(result) == 4

    def test_get_unregistered(self):
        """get should return None for unregistered effect."""
        assert EffectRegistry.get("nonexistent") is None

    def test_list(self):
        """list should return all registered effects."""
        items = EffectRegistry.list()
        assert len(items) >= 8

    def test_all_names(self):
        """all_names should list all effect names."""
        names = EffectRegistry.all_names()
        assert "rainbow" in names
        assert "aurora" in names
