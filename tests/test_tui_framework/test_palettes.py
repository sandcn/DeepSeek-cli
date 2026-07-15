"""Tests for tui_framework.core.palettes module."""
import pytest
from tui_framework.core.palettes import (
    GRADIENT_SUNSET, GRADIENT_OCEAN, GRADIENT_FOREST,
    GRADIENT_FIRE, GRADIENT_NEON, GRADIENT_AURORA,
    GRADIENT_SUNRISE, GRADIENT_PURPLE, GRADIENT_ICE,
    GRADIENT_ROSE, GRADIENT_LAVA, GRADIENT_GLACIER,
    GRADIENT_GOLD, GRADIENT_SKY, GRADIENT_MAGMA, GRADIENT_OCEAN_DEEP,
    BREATH_CYAN, BREATH_GREEN, BREATH_PURPLE,
    BREATH_GOLD, BREATH_ROSE,
)


class TestGradientPalettes:
    """Tests for pre-defined gradient palettes."""

    @pytest.mark.parametrize("palette", [
        GRADIENT_SUNSET, GRADIENT_OCEAN, GRADIENT_FOREST,
        GRADIENT_FIRE, GRADIENT_NEON, GRADIENT_AURORA,
        GRADIENT_SUNRISE, GRADIENT_PURPLE, GRADIENT_ICE,
        GRADIENT_ROSE, GRADIENT_LAVA, GRADIENT_GLACIER,
        GRADIENT_GOLD, GRADIENT_SKY, GRADIENT_MAGMA, GRADIENT_OCEAN_DEEP,
    ])
    def test_palette_not_empty(self, palette):
        """All palettes should be non-empty."""
        assert len(palette) > 0

    @pytest.mark.parametrize("palette", [
        GRADIENT_SUNSET, GRADIENT_OCEAN, GRADIENT_FOREST,
        GRADIENT_FIRE, GRADIENT_NEON, GRADIENT_AURORA,
        GRADIENT_SUNRISE, GRADIENT_PURPLE, GRADIENT_ICE,
        GRADIENT_ROSE, GRADIENT_LAVA, GRADIENT_GLACIER,
        GRADIENT_GOLD, GRADIENT_SKY, GRADIENT_MAGMA, GRADIENT_OCEAN_DEEP,
    ])
    def test_colors_in_valid_range(self, palette):
        """All colors should be in [0, 255]."""
        for c in palette:
            assert 0 <= c <= 255

    def test_gradient_sunset_length(self):
        """GRADIENT_SUNSET should have 8 steps."""
        assert len(GRADIENT_SUNSET) == 8

    def test_gradient_ocean_length(self):
        """GRADIENT_OCEAN should have 6 steps."""
        assert len(GRADIENT_OCEAN) == 6

    def test_gradient_fire_length(self):
        """GRADIENT_FIRE should have 9 steps."""
        assert len(GRADIENT_FIRE) == 9


class TestBreathPalettes:
    """Tests for breath palettes."""

    @pytest.mark.parametrize("palette", [
        BREATH_CYAN, BREATH_GREEN, BREATH_PURPLE,
        BREATH_GOLD, BREATH_ROSE,
    ])
    def test_breath_palette_is_symmetric(self, palette):
        """Breath palettes should have symmetric structure (12 steps)."""
        # All breath palettes are: gradient_range(a, b, 6) + gradient_range(b, a, 6)
        assert len(palette) == 12

    @pytest.mark.parametrize("palette", [
        BREATH_CYAN, BREATH_GREEN, BREATH_PURPLE,
        BREATH_GOLD, BREATH_ROSE,
    ])
    def test_breath_colors_in_range(self, palette):
        """All breath palette colors should be in [0, 255]."""
        for c in palette:
            assert 0 <= c <= 255

    def test_breath_cyan_first_is_dark(self):
        """Breath cyan should start dark."""
        assert BREATH_CYAN[0] < 50

    def test_breath_cyan_middle_is_bright(self):
        """Breath cyan should be bright in the middle."""
        mid = len(BREATH_CYAN) // 2
        assert BREATH_CYAN[mid] > 70
