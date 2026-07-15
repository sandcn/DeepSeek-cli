"""Tests for tui_framework.widgets.base module."""
import pytest
from tui_framework.widgets.base import TuiComponent, Widget, Writeable, _estimate_content_lines
from tui_framework.events.event_types import KeyPressEvent, MouseEvent, ResizeEvent, FocusEvent


class _MockAdapter:
    """Mock adapter that implements Writeable."""

    def __init__(self):
        self.written: list[str] = []

    def write(self, text: str) -> None:
        self.written.append(text)


class _TestComponent(TuiComponent):
    """Concrete component for testing."""

    def __init__(self, output: str = "test_output"):
        super().__init__()
        self._output = output

    def render(self) -> str:
        return self._output


class TestTuiComponent:
    """Tests for TuiComponent base class."""

    def test_initial_not_mounted(self):
        """New component should not be mounted."""
        comp = _TestComponent()
        assert not comp._mounted

    def test_did_mount(self):
        """did_mount should set _mounted=True."""
        comp = _TestComponent()
        comp.did_mount()
        assert comp._mounted

    def test_will_unmount(self):
        """will_unmount should set _mounted=False."""
        comp = _TestComponent()
        comp.did_mount()
        comp.will_unmount()
        assert not comp._mounted

    def test_should_update_default_true(self):
        """Default should_update should return True."""
        comp = _TestComponent()
        assert comp.should_update() is True

    def test_render_to_adapter(self):
        """render_to_adapter should write to adapter."""
        comp = _TestComponent("hello")
        adapter = _MockAdapter()
        comp.did_mount()
        lines = comp.render_to_adapter(adapter)
        assert lines >= 1
        assert "hello" in adapter.written[0]

    def test_render_to_adapter_not_mounted(self):
        """render_to_adapter should work even without mount."""
        comp = _TestComponent("test")
        adapter = _MockAdapter()
        comp.render_to_adapter(adapter)
        assert len(adapter.written) >= 1

    def test_render_to_adapter_should_update_false(self):
        """When should_update returns False, should not write."""

        class NoUpdateComponent(TuiComponent):
            def render(self) -> str:
                return "should_not_appear"

            def should_update(self, new_props=None) -> bool:
                return False

        comp = NoUpdateComponent()
        adapter = _MockAdapter()
        comp.render_to_adapter(adapter)
        assert len(adapter.written) == 0

    def test_multiline_render(self):
        """render_to_adapter should count lines correctly."""

        class MultiLineComponent(TuiComponent):
            def render(self) -> str:
                return "line1\nline2\nline3"

        comp = MultiLineComponent()
        adapter = _MockAdapter()
        lines = comp.render_to_adapter(adapter)
        assert lines == 3


class TestEstimateContentLines:
    """Tests for _estimate_content_lines."""

    def test_empty_string(self):
        """Empty string should return 1."""
        assert _estimate_content_lines("") == 1

    def test_single_line(self):
        """Single line should return 1."""
        assert _estimate_content_lines("hello") == 1

    def test_multiple_lines(self):
        """Multiple lines should return correct count."""
        assert _estimate_content_lines("a\nb\nc") == 3


class TestWriteableProtocol:
    """Tests for Writeable Protocol."""

    def test_mock_adapter_satisfies_protocol(self):
        """Mock adapter should satisfy Writeable."""
        assert isinstance(_MockAdapter(), Writeable)


# ── Widget 交互基类测试 ──────────────────────────────────


class _ConcreteWidget(Widget):
    """Concrete widget for testing Widget base class."""

    def render(self) -> str:
        return f"[widget:{self.widget_id}]"


class TestWidgetBasics:
    """Tests for Widget basic properties and initialization."""

    def test_widget_is_tui_component(self):
        """Widget should be a subclass of TuiComponent."""
        w = _ConcreteWidget()
        assert isinstance(w, TuiComponent)

    def test_default_unfocused(self):
        """New widget should be unfocused by default."""
        w = _ConcreteWidget()
        assert w.focused is False
        assert w._focused is False

    def test_default_enabled(self):
        """New widget should be enabled by default."""
        w = _ConcreteWidget()
        assert w.disabled is False

    def test_default_visible(self):
        """New widget should be visible by default."""
        w = _ConcreteWidget()
        assert w.visible is True

    def test_widget_has_unique_id(self):
        """Each widget should have a unique widget_id."""
        w1 = _ConcreteWidget()
        w2 = _ConcreteWidget()
        assert w1.widget_id != w2.widget_id
        assert len(w1.widget_id) == 12

    def test_widget_id_is_string(self):
        """widget_id should be a string."""
        w = _ConcreteWidget()
        assert isinstance(w.widget_id, str)
        assert len(w.widget_id) > 0


class TestWidgetFocus:
    """Tests for Widget focus/blur behavior."""

    def test_focus_sets_focused(self):
        """focus() should set _focused=True."""
        w = _ConcreteWidget()
        w.focus()
        assert w.focused is True

    def test_blur_unsets_focused(self):
        """blur() should set _focused=False."""
        w = _ConcreteWidget()
        w.focus()
        w.blur()
        assert w.focused is False

    def test_focus_idempotent(self):
        """focus() should be idempotent (no error on double focus)."""
        w = _ConcreteWidget()
        w.focus()
        w.focus()  # should not raise
        assert w.focused is True

    def test_blur_idempotent(self):
        """blur() should be idempotent (no error on double blur)."""
        w = _ConcreteWidget()
        w.blur()  # should not raise
        assert w.focused is False

    def test_on_focus_called(self):
        """on_focus() hook should be called when focused."""

        class FocusHookWidget(Widget):
            def render(self) -> str:
                return ""
            def on_focus(self) -> None:
                self._focus_called = True

        w = FocusHookWidget()
        w._focus_called = False
        w.focus()
        assert w._focus_called is True

    def test_on_blur_called(self):
        """on_blur() hook should be called when blurred."""

        class BlurHookWidget(Widget):
            def render(self) -> str:
                return ""
            def on_blur(self) -> None:
                self._blur_called = True

        w = BlurHookWidget()
        w._blur_called = False
        w.focus()
        w.blur()
        assert w._blur_called is True

    def test_on_focus_exception_isolated(self):
        """Exception in on_focus should not propagate."""

        class BadFocusWidget(Widget):
            def render(self) -> str:
                return ""
            def on_focus(self) -> None:
                raise RuntimeError("focus crash")

        w = BadFocusWidget()
        # should not raise
        w.focus()
        assert w.focused is True  # state should still be set


class TestWidgetEnableDisable:
    """Tests for Widget enable/disable."""

    def test_enable(self):
        """enable() should set disabled=False."""
        w = _ConcreteWidget()
        w.disable()
        w.enable()
        assert w.disabled is False

    def test_disable(self):
        """disable() should set disabled=True."""
        w = _ConcreteWidget()
        w.disable()
        assert w.disabled is True

    def test_disabled_widget_ignores_key(self):
        """Disabled widget should ignore key events."""
        w = _ConcreteWidget()
        w.disable()
        ev = KeyPressEvent(key="enter")
        assert w.handle_key(ev) is False

    def test_disabled_widget_ignores_mouse(self):
        """Disabled widget should ignore mouse events."""
        w = _ConcreteWidget()
        w.disable()
        ev = MouseEvent(x=10, y=5, button="left", action="click")
        assert w.handle_mouse(ev) is False


class TestWidgetVisibility:
    """Tests for Widget show/hide."""

    def test_show(self):
        """show() should set visible=True."""
        w = _ConcreteWidget()
        w.hide()
        w.show()
        assert w.visible is True

    def test_hide(self):
        """hide() should set visible=False."""
        w = _ConcreteWidget()
        w.hide()
        assert w.visible is False

    def test_hidden_widget_ignores_key(self):
        """Hidden widget should ignore key events."""
        w = _ConcreteWidget()
        w.hide()
        ev = KeyPressEvent(key="enter")
        assert w.handle_key(ev) is False

    def test_hidden_widget_ignores_mouse(self):
        """Hidden widget should ignore mouse events."""
        w = _ConcreteWidget()
        w.hide()
        ev = MouseEvent(x=5, y=3, button="left", action="click")
        assert w.handle_mouse(ev) is False


class TestWidgetKeyHandling:
    """Tests for Widget handle_key / on_key."""

    def test_handle_key_returns_bool(self):
        """handle_key should return a bool."""
        w = _ConcreteWidget()
        ev = KeyPressEvent(key="a")
        result = w.handle_key(ev)
        assert isinstance(result, bool)

    def test_default_on_key_returns_false(self):
        """Default on_key should return False (not consumed)."""
        w = _ConcreteWidget()
        ev = KeyPressEvent(key="x")
        assert w.on_key(ev) is False

    def test_on_key_override_consumes(self):
        """Overridden on_key should allow event consumption."""

        class KeyConsumingWidget(Widget):
            def render(self) -> str:
                return ""
            def on_key(self, event: KeyPressEvent) -> bool:
                return event.key == "enter"

        w = KeyConsumingWidget()
        assert w.handle_key(KeyPressEvent(key="a")) is False
        assert w.handle_key(KeyPressEvent(key="enter")) is True

    def test_handle_key_with_modifiers(self):
        """on_key should receive modifier information."""

        captured_ctrl = None

        class ModCaptureWidget(Widget):
            def render(self) -> str:
                return ""
            def on_key(self, event: KeyPressEvent) -> bool:
                nonlocal captured_ctrl
                captured_ctrl = event.ctrl
                return True

        w = ModCaptureWidget()
        w.handle_key(KeyPressEvent(key="c", ctrl=True))
        assert captured_ctrl is True

    def test_on_key_exception_isolated(self):
        """Exception in on_key should not propagate."""

        class CrashKeyWidget(Widget):
            def render(self) -> str:
                return ""
            def on_key(self, event: KeyPressEvent) -> bool:
                raise RuntimeError("key crash")

        w = CrashKeyWidget()
        # should not raise
        result = w.handle_key(KeyPressEvent(key="x"))
        assert result is False


class TestWidgetMouseHandling:
    """Tests for Widget handle_mouse / on_mouse."""

    def test_handle_mouse_returns_bool(self):
        """handle_mouse should return a bool."""
        w = _ConcreteWidget()
        ev = MouseEvent(x=5, y=5, button="left", action="click")
        result = w.handle_mouse(ev)
        assert isinstance(result, bool)

    def test_default_on_mouse_returns_false(self):
        """Default on_mouse should return False."""
        w = _ConcreteWidget()
        ev = MouseEvent(x=10, y=3, button="left", action="click")
        assert w.on_mouse(ev) is False

    def test_on_mouse_override_consumes(self):
        """Overridden on_mouse should allow event consumption."""

        class MouseConsumingWidget(Widget):
            def render(self) -> str:
                return ""
            def on_mouse(self, event: MouseEvent) -> bool:
                return event.button == "left"

        w = MouseConsumingWidget()
        assert w.handle_mouse(MouseEvent(button="left", action="click")) is True
        assert w.handle_mouse(MouseEvent(button="right", action="click")) is False

    def test_on_mouse_exception_isolated(self):
        """Exception in on_mouse should not propagate."""

        class CrashMouseWidget(Widget):
            def render(self) -> str:
                return ""
            def on_mouse(self, event: MouseEvent) -> bool:
                raise RuntimeError("mouse crash")

        w = CrashMouseWidget()
        result = w.handle_mouse(MouseEvent(button="left", action="click"))
        assert result is False


class TestWidgetLifecycle:
    """Tests for Widget lifecycle (TuiComponent integration)."""

    def test_widget_inherits_did_mount(self):
        """Widget should inherit did_mount from TuiComponent."""
        w = _ConcreteWidget()
        assert w._mounted is False
        w.did_mount()
        assert w._mounted is True

    def test_widget_inherits_will_unmount(self):
        """Widget should inherit will_unmount from TuiComponent."""
        w = _ConcreteWidget()
        w.did_mount()
        w.will_unmount()
        assert w._mounted is False

    def test_widget_renders(self):
        """Widget should render via inherited render_to_adapter."""
        w = _ConcreteWidget()
        adapter = _MockAdapter()
        w.render_to_adapter(adapter)
        assert len(adapter.written) >= 1
        assert w.widget_id in adapter.written[0]
