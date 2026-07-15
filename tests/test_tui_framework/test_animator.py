"""Tests for tui_framework.core.animator module."""
import pytest
from tui_framework.core.animator import AnimatorContext, BreathPalette


class TestAnimatorContext:
    """Tests for AnimatorContext singleton."""

    def setup_method(self):
        """Reset before each test."""
        AnimatorContext.reset_default()

    def test_get_default_returns_same_instance(self):
        """Multiple calls should return the same instance."""
        a1 = AnimatorContext.get_default()
        a2 = AnimatorContext.get_default()
        assert a1 is a2

    def test_initial_frame_is_zero(self):
        """Initial frame should be 0."""
        animator = AnimatorContext.get_default()
        assert animator.frame == 0

    def test_tick_increments_frame(self):
        """tick should increment frame."""
        animator = AnimatorContext.get_default()
        animator.tick()
        assert animator.frame == 1

    def test_tick_with_delta(self):
        """tick with delta should increment by delta."""
        animator = AnimatorContext.get_default()
        animator.tick(5)
        assert animator.frame == 5

    def test_breath_frame(self):
        """breath_frame should wrap around."""
        animator = AnimatorContext.get_default()
        animator.breath_cycle_len = 12
        animator.tick(13)
        assert animator.breath_frame == 1  # 13 % 12

    def test_pulse_frame(self):
        """pulse_frame should wrap around."""
        animator = AnimatorContext.get_default()
        animator.tick(5)
        assert animator.pulse_frame == 1  # 5 % 4

    def test_sine_breath_range(self):
        """sine_breath should be in [0, 1]."""
        animator = AnimatorContext.get_default()
        for _ in range(100):
            animator.tick()
            assert 0.0 <= animator.sine_breath <= 1.0

    def test_sine_pulse_range(self):
        """sine_pulse should be in [0, 1]."""
        animator = AnimatorContext.get_default()
        for _ in range(100):
            animator.tick()
            assert 0.0 <= animator.sine_pulse <= 1.0

    def test_sine_color_range(self):
        """sine_color should return valid colors."""
        animator = AnimatorContext.get_default()
        for _ in range(50):
            animator.tick()
            c = animator.sine_color(0, 100)
            assert 0 <= c <= 100

    def test_sine_color_seq(self):
        """sine_color_seq should interpolate within list."""
        animator = AnimatorContext.get_default()
        colors = [0, 50, 100]
        result = animator.sine_color_seq(colors)
        assert 0 <= result <= 100

    def test_reset_default(self):
        """reset_default should clear the instance."""
        a1 = AnimatorContext.get_default()
        AnimatorContext.reset_default()
        a2 = AnimatorContext.get_default()
        assert a1 is not a2
        assert a2.frame == 0


class TestBreathPalette:
    """Tests for BreathPalette registry."""

    def test_get_registered(self):
        """Pre-registered palette should be accessible."""
        colors = BreathPalette.get("think")
        assert len(colors) > 0

    def test_get_color(self):
        """get_color should return valid color."""
        c = BreathPalette.get_color("think", 0)
        assert 0 <= c <= 255

    def test_get_missing(self):
        """Missing palette should return empty list."""
        assert BreathPalette.get("nonexistent") == []

    def test_get_color_missing(self):
        """Missing palette should return fallback 45."""
        assert BreathPalette.get_color("nonexistent", 0) == 45

    def test_has(self):
        """has should return correct boolean."""
        assert BreathPalette.has("think")
        assert not BreathPalette.has("nonexistent")

    def test_register_and_get(self):
        """Register then get should return same colors."""
        BreathPalette.register("test_custom", [10, 20, 30])
        assert BreathPalette.get("test_custom") == [10, 20, 30]

    def test_get_sine_color(self):
        """get_sine_color should return valid color."""
        c = BreathPalette.get_sine_color("think", 5)
        assert 0 <= c <= 255
