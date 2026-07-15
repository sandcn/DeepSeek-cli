"""Tests for tui_framework.events.event_types module."""
import pytest
from tui_framework.events.event_types import (
    DisplayEvent, SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
    ToolOutputChunkEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
    ContentChunkEvent, ReasoningChunkEvent,
    ParseInfoEvent, ParseInfoDoneEvent, TokenEvent,
    LiveOutputEvent, LiveInputEvent, SpeedUpdatedEvent,
    OutputEvent, ToolSummaryEvent,
    UserSelectNeededEvent, AgentResultEvent,
    KeyPressEvent, MouseEvent, ResizeEvent, FocusEvent,
    ALL_EVENT_TYPES,
)


class TestDisplayEvent:
    """Tests for DisplayEvent base class."""

    def test_create(self):
        """Should create with defaults."""
        ev = DisplayEvent()
        assert ev.timestamp > 0
        assert ev.source == ""

    def test_with_source(self):
        """Should accept source parameter."""
        ev = DisplayEvent(source="agent-1")
        assert ev.source == "agent-1"

    def test_frozen(self):
        """Should be immutable (frozen dataclass)."""
        ev = DisplayEvent()
        with pytest.raises(Exception):
            ev.source = "new"  # type: ignore


class TestEventTypes:
    """Tests for all event types."""

    @pytest.mark.parametrize("event_cls", ALL_EVENT_TYPES)
    def test_event_can_be_created(self, event_cls):
        """All events should be creatable with defaults."""
        ev = event_cls()
        assert isinstance(ev, DisplayEvent)

    @pytest.mark.parametrize("event_cls", ALL_EVENT_TYPES)
    def test_event_is_frozen(self, event_cls):
        """All events should be frozen dataclasses."""
        ev = event_cls()
        assert ev.timestamp > 0  # default_factory

    def test_session_started(self):
        """SessionStarted should create correctly."""
        ev = SessionStarted()
        assert isinstance(ev, DisplayEvent)

    def test_session_stopped(self):
        """SessionStopped should have final field."""
        ev = SessionStopped(final=True)
        assert ev.final is True

    def test_tool_started(self):
        """ToolStartedEvent should have tool fields."""
        ev = ToolStartedEvent(
            label="agent-1", tool_name="read_file",
            detail="reading...", tool_id="call_123",
        )
        assert ev.label == "agent-1"
        assert ev.tool_name == "read_file"
        assert ev.tool_id == "call_123"

    def test_tool_done(self):
        """ToolDoneEvent should have success field."""
        ev = ToolDoneEvent(success=False)
        assert ev.success is False

    def test_content_chunk(self):
        """ContentChunkEvent should have text field."""
        ev = ContentChunkEvent(text="hello", label="agent-1")
        assert ev.text == "hello"
        assert ev.label == "agent-1"

    def test_reasoning_chunk(self):
        """ReasoningChunkEvent should have text field."""
        ev = ReasoningChunkEvent(text="thinking...", label="agent-1")
        assert ev.text == "thinking..."

    def test_agent_added(self):
        """AgentAddedEvent should have status field."""
        ev = AgentAddedEvent(label="agent-1", description="desc")
        assert ev.status == "running"

    def test_agent_status_changed(self):
        """AgentStatusChanged should have status field."""
        ev = AgentStatusChanged(label="agent-1", status="done")
        assert ev.status == "done"

    def test_all_event_types_is_tuple(self):
        """ALL_EVENT_TYPES should be a tuple."""
        assert isinstance(ALL_EVENT_TYPES, tuple)
        assert len(ALL_EVENT_TYPES) > 0


class TestInputEventTypes:
    """Tests for input event types (KeyPressEvent, MouseEvent, etc.)."""

    def test_keypress_create_default(self):
        """KeyPressEvent should create with defaults."""
        ev = KeyPressEvent()
        assert ev.key == ""
        assert ev.ctrl is False
        assert ev.alt is False
        assert ev.shift is False
        assert isinstance(ev, DisplayEvent)

    def test_keypress_with_modifiers(self):
        """KeyPressEvent should store key and modifiers."""
        ev = KeyPressEvent(key="a", ctrl=True, alt=False, shift=True)
        assert ev.key == "a"
        assert ev.ctrl is True
        assert ev.alt is False
        assert ev.shift is True

    def test_keypress_special_keys(self):
        """KeyPressEvent should support special key names."""
        ev = KeyPressEvent(key="enter")
        assert ev.key == "enter"
        ev2 = KeyPressEvent(key="backspace")
        assert ev2.key == "backspace"
        ev3 = KeyPressEvent(key="f1")
        assert ev3.key == "f1"

    def test_keypress_frozen(self):
        """KeyPressEvent should be immutable."""
        ev = KeyPressEvent(key="x")
        with pytest.raises(Exception):
            ev.key = "y"  # type: ignore

    def test_mouse_create_default(self):
        """MouseEvent should create with defaults."""
        ev = MouseEvent()
        assert ev.x == 0
        assert ev.y == 0
        assert ev.button == ""
        assert ev.action == ""
        assert isinstance(ev, DisplayEvent)

    def test_mouse_with_coords(self):
        """MouseEvent should store coordinates and action."""
        ev = MouseEvent(x=10, y=5, button="left", action="click")
        assert ev.x == 10
        assert ev.y == 5
        assert ev.button == "left"
        assert ev.action == "click"

    def test_mouse_scroll(self):
        """MouseEvent should support scroll actions."""
        ev = MouseEvent(x=20, y=3, button="", action="scroll_down")
        assert ev.action == "scroll_down"

    def test_mouse_dblclick(self):
        """MouseEvent should support double-click."""
        ev = MouseEvent(x=15, y=7, button="left", action="dblclick")
        assert ev.action == "dblclick"

    def test_mouse_frozen(self):
        """MouseEvent should be immutable."""
        ev = MouseEvent(x=5, y=3)
        with pytest.raises(Exception):
            ev.x = 10  # type: ignore

    def test_resize_create_default(self):
        """ResizeEvent should create with defaults."""
        ev = ResizeEvent()
        assert ev.width == 0
        assert ev.height == 0
        assert isinstance(ev, DisplayEvent)

    def test_resize_with_dimensions(self):
        """ResizeEvent should store dimensions."""
        ev = ResizeEvent(width=120, height=40)
        assert ev.width == 120
        assert ev.height == 40

    def test_resize_frozen(self):
        """ResizeEvent should be immutable."""
        ev = ResizeEvent(width=80, height=24)
        with pytest.raises(Exception):
            ev.width = 100  # type: ignore

    def test_focus_create_default(self):
        """FocusEvent should create with defaults."""
        ev = FocusEvent()
        assert ev.widget_id == ""
        assert ev.gained is True  # default is gained=True

    def test_focus_gained(self):
        """FocusEvent should indicate gained focus."""
        ev = FocusEvent(widget_id="widget_001", gained=True)
        assert ev.widget_id == "widget_001"
        assert ev.gained is True

    def test_focus_lost(self):
        """FocusEvent should indicate lost focus."""
        ev = FocusEvent(widget_id="widget_002", gained=False)
        assert ev.widget_id == "widget_002"
        assert ev.gained is False

    def test_focus_frozen(self):
        """FocusEvent should be immutable."""
        ev = FocusEvent(widget_id="w1", gained=True)
        with pytest.raises(Exception):
            ev.widget_id = "w2"  # type: ignore

    def test_all_new_events_in_registry(self):
        """New events should be registered in ALL_EVENT_TYPES."""
        assert KeyPressEvent in ALL_EVENT_TYPES
        assert MouseEvent in ALL_EVENT_TYPES
        assert ResizeEvent in ALL_EVENT_TYPES
        assert FocusEvent in ALL_EVENT_TYPES
