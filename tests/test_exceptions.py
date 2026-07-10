"""Tests for src/core/exceptions.py – exception classification."""

from __future__ import annotations

import asyncio

import pytest

from src.core.exceptions import (
    FatalError,
    NonFatalError,
    TransientError,
    is_fatal_exception,
)


# ---------------------------------------------------------------------------
# Fatal exceptions
# ---------------------------------------------------------------------------

class TestFatalDetection:
    """Verify that clearly fatal conditions are detected."""

    def test_memory_error_is_fatal(self) -> None:
        assert is_fatal_exception(MemoryError()) is True

    def test_system_exit_is_fatal(self) -> None:
        assert is_fatal_exception(SystemExit(1)) is True

    def test_fatal_error_subclass_is_fatal(self) -> None:
        assert is_fatal_exception(FatalError("disk corrupted")) is True

    @pytest.mark.parametrize(
        "msg",
        [
            "[Errno 28] No space left on device",
            "write failed: [Errno 28]",
            "[Errno 12] Cannot allocate memory",
            "mmap failed [Errno 12]",
        ],
    )
    def test_os_error_with_fatal_errno(self, msg: str) -> None:
        assert is_fatal_exception(OSError(msg)) is True


# ---------------------------------------------------------------------------
# Non-fatal exceptions
# ---------------------------------------------------------------------------

class TestNonFatalDetection:
    """Verify that recoverable errors are NOT treated as fatal."""

    def test_connection_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(ConnectionError("reset")) is False

    def test_timeout_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(TimeoutError("timed out")) is False

    def test_asyncio_timeout_is_non_fatal(self) -> None:
        assert is_fatal_exception(asyncio.TimeoutError()) is False

    def test_value_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(ValueError("bad input")) is False

    def test_key_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(KeyError("missing")) is False

    def test_non_fatal_error_subclass_is_non_fatal(self) -> None:
        assert is_fatal_exception(NonFatalError("minor issue")) is False

    def test_transient_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(TransientError("rate limited")) is False

    def test_generic_exception_is_non_fatal(self) -> None:
        """Unknown exception types default to non-fatal (conservative)."""
        assert is_fatal_exception(RuntimeError("something")) is False

    def test_generic_exception_subclass_is_non_fatal(self) -> None:
        """Custom exception without explicit classification defaults to non-fatal."""

        class MyPluginError(Exception):
            pass

        assert is_fatal_exception(MyPluginError("oops")) is False


# ---------------------------------------------------------------------------
# CancelledError special case
# ---------------------------------------------------------------------------

class TestCancelledError:
    """CancelledError must NEVER be classified as fatal."""

    def test_cancelled_error_is_not_fatal(self) -> None:
        assert is_fatal_exception(asyncio.CancelledError()) is False


# ---------------------------------------------------------------------------
# OSError edge cases
# ---------------------------------------------------------------------------

class TestOSErrorEdgeCases:
    """OSError without fatal errno patterns should be non-fatal."""

    def test_permission_denied_is_non_fatal(self) -> None:
        assert is_fatal_exception(PermissionError("[Errno 13] Permission denied")) is False

    def test_file_not_found_is_non_fatal(self) -> None:
        assert is_fatal_exception(FileNotFoundError("[Errno 2] No such file")) is False

    def test_generic_os_error_is_non_fatal(self) -> None:
        assert is_fatal_exception(OSError("unknown error")) is False


# ---------------------------------------------------------------------------
# Hierarchy sanity checks
# ---------------------------------------------------------------------------

class TestHierarchy:
    """Ensure exception class relationships are correct."""

    def test_transient_is_non_fatal(self) -> None:
        assert issubclass(TransientError, NonFatalError)

    def test_non_fatal_is_exception(self) -> None:
        assert issubclass(NonFatalError, Exception)

    def test_fatal_is_exception(self) -> None:
        assert issubclass(FatalError, Exception)

    def test_fatal_is_not_non_fatal(self) -> None:
        """FatalError and NonFatalError are separate branches."""
        assert not issubclass(FatalError, NonFatalError)
        assert not issubclass(NonFatalError, FatalError)
