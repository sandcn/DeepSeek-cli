"""Lifecycle management — start/stop/suspend/resume, subsystem factory."""
from .lifecycle import ChatUILifecycle  # noqa: F401
from .subsystem_factory import (  # noqa: F401
    create_bottom_bar,
    create_cursor_tracker,
    create_completion_engine,
    get_event_bus,
)
