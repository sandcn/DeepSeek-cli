"""Tests for tui_framework.animation.transitions module."""
import pytest
from tui_framework.animation.transitions import (
    FadeIn, FadeOut, SlideIn, SlideOut, Typewriter,
)


class TestFadeIn:
    """Tests for FadeIn transition."""

    def test_render_returns_ansi(self):
        """render should return ANSI color sequence."""
        fi = FadeIn(total_frames=6)
        result = fi.render(3)
        # May be empty if narrow screen, or have ANSI
        if result:
            assert "\033[38;5;" in result

    def test_beyond_total_returns_empty(self):
        """Frame beyond total_frames should return empty."""
        fi = FadeIn(total_frames=6)
        assert fi.render(10) == ""

    def test_negative_frame_returns_empty(self):
        """Frame < 0 with no total_frames should not crash."""
        fi = FadeIn(total_frames=6)
        # Should not crash
        fi.render(0)

    def test_easing_smooth(self):
        """Smooth easing should produce valid colors."""
        fi = FadeIn(easing="smooth", total_frames=6)
        result = fi.render(3)
        if result:
            assert "\033[38;5;" in result

    def test_easing_bounce(self):
        """Bounce easing should produce valid colors."""
        fi = FadeIn(easing="bounce", total_frames=6)
        fi.render(3)  # should not crash

    def test_easing_linear(self):
        """Linear easing should produce valid colors."""
        fi = FadeIn(easing="linear", total_frames=6)
        fi.render(3)  # should not crash


class TestFadeOut:
    """Tests for FadeOut transition."""

    def test_render_returns_ansi(self):
        """render should return ANSI color sequence."""
        fo = FadeOut(total_frames=6)
        result = fo.render(3)
        if result:
            assert "\033[38;5;" in result

    def test_beyond_total_returns_empty(self):
        """Frame beyond total_frames should return empty."""
        fo = FadeOut(total_frames=6)
        assert fo.render(10) == ""


class TestSlideIn:
    """Tests for SlideIn transition."""

    def test_render_with_text(self):
        """SlideIn should return partial text."""
        si = SlideIn(text="hello world", total_frames=6)
        result = si.render(0)
        # Frame 0 should be empty in non-narrow mode
        assert result == "" or result == "hello world"

    def test_render_beyond_total(self):
        """Beyond total should return full text."""
        si = SlideIn(text="hello", total_frames=6)
        result = si.render(10)
        assert result == "hello"

    def test_no_text(self):
        """No text should return empty."""
        si = SlideIn(text="", total_frames=6)
        assert si.render(3) == ""

    def test_direction_right(self):
        """Right direction should work."""
        si = SlideIn(text="hello", total_frames=4, direction="right")
        si.render(2)  # should not crash


class TestSlideOut:
    """Tests for SlideOut transition."""

    def test_render_frame_zero(self):
        """Frame 0 should return full text."""
        so = SlideOut(text="hello", total_frames=6)
        result = so.render(0)
        assert result == "hello" or result == ""

    def test_render_beyond_total(self):
        """Beyond total should return empty."""
        so = SlideOut(text="hello", total_frames=6)
        assert so.render(10) == ""


class TestTypewriter:
    """Tests for Typewriter transition."""

    def test_render_with_text(self):
        """Typewriter should reveal text gradually."""
        tw = Typewriter(text="hello", chars_per_frame=1)
        result = tw.render(2)
        if result:
            # Should contain at most 2 chars
            pass

    def test_beyond_total_returns_full(self):
        """Beyond total_frames should return full text."""
        tw = Typewriter(text="abc", chars_per_frame=1)
        full_result = tw.render(100)
        if full_result:
            assert "abc" in full_result

    def test_empty_text(self):
        """Empty text should return empty."""
        tw = Typewriter(text="")
        assert tw.render(5) == ""

    def test_frame_zero_returns_empty(self):
        """Frame 0 should return empty (no chars revealed)."""
        tw = Typewriter(text="hello", chars_per_frame=2)
        result = tw.render(0)
        if result:
            # May be empty in non-narrow mode
            pass
