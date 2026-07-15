"""Tests for tui_framework.animation.composer module."""
import pytest
from tui_framework.animation.composer import (
    AnimationEffect, CompositeEffect, EffectChain,
    InterleaveEffect, anim_parallel, anim_sequence, anim_loop,
)


class _MockEffect:
    """Mock effect that returns predictable output."""

    def __init__(self, prefix: str = "FX"):
        self.prefix = prefix
        self.frames: list[str] = []

    def render(self, frame: int) -> str:
        result = f"{self.prefix}:{frame}"
        self.frames.append(result)
        return result


class TestCompositeEffect:
    """Tests for CompositeEffect."""

    def test_parallel_rendering(self):
        """All sub-effects should render in each frame."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        ce = CompositeEffect([a, b], separator="|")
        result = ce.render(0)
        assert "A:0" in result
        assert "B:0" in result
        assert "|" in result

    def test_empty_effects(self):
        """Empty effects should return empty string."""
        ce = CompositeEffect([], separator=" ")
        assert ce.render(0) == ""

    def test_single_effect(self):
        """Single effect should work."""
        a = _MockEffect("A")
        ce = CompositeEffect([a])
        result = ce.render(5)
        assert result == "A:5"

    def test_all_empty_results(self):
        """If all sub-effects return empty, result should be empty."""

        class EmptyEffect:
            def render(self, frame: int) -> str:
                return ""

        ce = CompositeEffect([EmptyEffect(), EmptyEffect()])
        assert ce.render(0) == ""


class TestEffectChain:
    """Tests for EffectChain."""

    def test_sequential_playback(self):
        """Effects should play in sequence."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        chain = EffectChain([a, b], [3, 3], loop=False)
        # First 3 frames: effect A
        assert "A:0" in chain.render(0)
        assert "A:2" in chain.render(2)
        # Frame 3 onward: effect B
        assert "B:0" in chain.render(3)
        assert "B:2" in chain.render(5)

    def test_loop(self):
        """Loop should wrap around."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        chain = EffectChain([a, b], [2, 2], loop=True)
        # Frame 0-1: A, 2-3: B, 4-5: A again
        assert "A:0" in chain.render(0)
        assert "B:0" in chain.render(2)
        assert "A:0" in chain.render(4)

    def test_length_mismatch_raises(self):
        """Mismatched effects/durations should raise."""
        with pytest.raises(ValueError):
            EffectChain([_MockEffect("A")], [1, 2], loop=False)

    def test_empty_effects(self):
        """Empty effects should return empty string."""
        chain = EffectChain([], [], loop=False)
        assert chain.render(0) == ""


class TestInterleaveEffect:
    """Tests for InterleaveEffect."""

    def test_alternates(self):
        """Should alternate between a and b."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        ie = InterleaveEffect(a, b, interval=1)
        assert "A" in ie.render(0)
        assert "B" in ie.render(1)
        assert "A" in ie.render(2)

    def test_interval(self):
        """interval should control alternation frequency."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        ie = InterleaveEffect(a, b, interval=3)
        assert "A" in ie.render(0)
        assert "A" in ie.render(2)
        assert "B" in ie.render(3)


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_anim_parallel(self):
        """anim_parallel should create CompositeEffect."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        result = anim_parallel(a, b)
        assert isinstance(result, CompositeEffect)

    def test_anim_sequence(self):
        """anim_sequence should create EffectChain."""
        a = _MockEffect("A")
        b = _MockEffect("B")
        result = anim_sequence(a, b)
        assert isinstance(result, EffectChain)

    def test_anim_sequence_empty_raises(self):
        """Empty anim_sequence should raise."""
        with pytest.raises(ValueError):
            anim_sequence()

    def test_anim_loop(self):
        """anim_loop should create loop effect."""
        a = _MockEffect("A")
        result = anim_loop(a, period=4)
        assert result.render(0) == "A:0"
        assert result.render(4) == "A:0"  # should wrap


class TestAnimationEffectProtocol:
    """Tests for AnimationEffect Protocol."""

    def test_mock_effect_satisfies_protocol(self):
        """Mock effect should satisfy AnimationEffect."""
        assert isinstance(_MockEffect(), AnimationEffect)

    def test_composite_effect_satisfies_protocol(self):
        """CompositeEffect should satisfy AnimationEffect."""
        ce = CompositeEffect([_MockEffect()])
        assert isinstance(ce, AnimationEffect)
