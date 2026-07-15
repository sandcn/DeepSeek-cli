"""Tests for tui_framework.framework module."""
import pytest
from tui_framework.framework import (
    Framework, create_component, frame_from_context, get_animator,
)
from tui_framework.widgets.base import TuiComponent


class _TestWidget(TuiComponent):
    """Test widget for framework tests."""

    def __init__(self, label: str = "test"):
        super().__init__()
        self.label = label

    def render(self) -> str:
        return self.label


class TestFramework:
    """Tests for Framework singleton."""

    def setup_method(self):
        """Reset before each test."""
        Framework.reset_default()

    def test_get_default_singleton(self):
        """get_default should return same instance."""
        f1 = Framework.get_default()
        f2 = Framework.get_default()
        assert f1 is f2

    def test_reset_default(self):
        """reset_default should clear instance."""
        f1 = Framework.get_default()
        Framework.reset_default()
        f2 = Framework.get_default()
        assert f1 is not f2

    def test_create_component(self):
        """create_component should mount component."""
        fw = Framework.get_default()
        widget = fw.create_component(_TestWidget, label="hello")
        assert widget._mounted
        assert widget.label == "hello"

    def test_get_registry(self):
        """get_registry should return EffectRegistry."""
        fw = Framework.get_default()
        registry = fw.get_registry()
        assert registry is not None
        # EffectRegistry should have pre-registered effects
        assert hasattr(registry, 'has')

    def test_get_stylesheet(self):
        """get_stylesheet should return StyleSheet."""
        fw = Framework.get_default()
        stylesheet = fw.get_stylesheet()
        assert stylesheet is not None

    def test_get_animator(self):
        """get_animator should return AnimatorContext."""
        fw = Framework.get_default()
        animator = fw.get_animator()
        assert animator is not None
        assert hasattr(animator, 'frame')

    def test_get_frame(self):
        """get_frame should return current frame number."""
        fw = Framework.get_default()
        frame = fw.get_frame()
        assert isinstance(frame, int)
        assert frame >= 0

    def test_get_frame_after_tick(self):
        """get_frame should reflect tick changes."""
        fw = Framework.get_default()
        animator = fw.get_animator()
        animator.tick(5)
        assert fw.get_frame() == 5


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def setup_method(self):
        Framework.reset_default()

    def test_create_component_function(self):
        """create_component function should work."""
        widget = create_component(_TestWidget, label="test")
        assert widget._mounted
        assert widget.label == "test"

    def test_frame_from_context(self):
        """frame_from_context should return valid frame."""
        frame = frame_from_context()
        assert isinstance(frame, int)

    def test_get_animator_function(self):
        """get_animator function should return animator."""
        animator = get_animator()
        assert animator is not None
        assert hasattr(animator, 'frame')
