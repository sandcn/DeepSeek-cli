"""Tests for tui_framework.events.event_bus module."""
import pytest
from tui_framework.events.event_bus import DisplayEventBus
from tui_framework.events.event_types import DisplayEvent, ContentChunkEvent


class TestDisplayEventBus:
    """Tests for DisplayEventBus (framework standalone)."""

    def setup_method(self):
        """Reset before each test."""
        DisplayEventBus.reset_default()

    def test_get_default_singleton(self):
        """get_default should return same instance."""
        bus1 = DisplayEventBus.get_default()
        bus2 = DisplayEventBus.get_default()
        assert bus1 is bus2

    def test_subscribe_and_publish(self):
        """Subscribe then publish should deliver event."""
        received = []

        def handler(event):
            received.append(event)

        bus = DisplayEventBus()
        bus.subscribe(handler)
        ev = DisplayEvent()
        bus.publish(ev)
        assert len(received) == 1
        assert received[0] is ev

    def test_subscribe_by_type(self):
        """Type-specific subscription should filter events."""
        received = []

        def handler(event):
            received.append(event)

        bus = DisplayEventBus()
        bus.subscribe(handler, ContentChunkEvent)
        bus.publish(DisplayEvent())  # should not match
        assert len(received) == 0
        bus.publish(ContentChunkEvent(text="test"))  # should match
        assert len(received) == 1

    def test_unsubscribe(self):
        """Unsubscribe should stop delivery."""
        received = []

        def handler(event):
            received.append(event)

        bus = DisplayEventBus()
        bus.subscribe(handler)
        bus.publish(DisplayEvent())
        assert len(received) == 1
        bus.unsubscribe(handler)
        bus.publish(DisplayEvent())
        assert len(received) == 1  # no new events

    def test_unsubscribe_by_type(self):
        """Unsubscribe by type should work."""
        received = []

        def handler(event):
            received.append(event)

        bus = DisplayEventBus()
        bus.subscribe(handler, ContentChunkEvent)
        bus.unsubscribe(handler, ContentChunkEvent)
        bus.publish(ContentChunkEvent(text="test"))
        assert len(received) == 0

    def test_clear(self):
        """clear should remove all subscriptions."""
        received = []

        def handler(event):
            received.append(event)

        bus = DisplayEventBus()
        bus.subscribe(handler)
        bus.clear()
        bus.publish(DisplayEvent())
        assert len(received) == 0

    def test_subscriber_count(self):
        """subscriber_count should track subscriptions."""
        bus = DisplayEventBus()
        assert bus.subscriber_count() == 0
        bus.subscribe(lambda e: None)
        assert bus.subscriber_count() == 1

    def test_type_subscription_count(self):
        """Type subscriptions should be counted."""
        bus = DisplayEventBus()
        bus.subscribe(lambda e: None, ContentChunkEvent)
        assert bus.subscriber_count() == 1

    def test_handler_exception_does_not_crash(self):
        """Handler exception should not propagate."""
        def failing_handler(event):
            raise RuntimeError("test error")

        def ok_handler(event):
            pass

        bus = DisplayEventBus()
        bus.subscribe(failing_handler)
        bus.subscribe(ok_handler)
        # Should not raise
        bus.publish(DisplayEvent())

    def test_invalid_event_type_raises(self):
        """Subscribing with non-DisplayEvent type should raise."""
        bus = DisplayEventBus()
        with pytest.raises(TypeError):
            bus.subscribe(lambda e: None, str)  # type: ignore

    def test_reset_default(self):
        """reset_default should clear instance."""
        bus1 = DisplayEventBus.get_default()
        DisplayEventBus.reset_default()
        bus2 = DisplayEventBus.get_default()
        assert bus1 is not bus2
