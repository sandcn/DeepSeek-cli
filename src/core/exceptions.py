"""
Core exception classification for CLI message consumer.

Provides a hierarchy of exceptions to distinguish fatal vs non-fatal errors,
enabling the CLI interaction loop to continue running after recoverable failures.
"""

from __future__ import annotations

import asyncio
import re
from typing import Union


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class NonFatalError(Exception):
    """Non-fatal exception: the application can continue running.

    Examples: network blips, single API failures, message format errors.
    """


class TransientError(NonFatalError):
    """Transient error that may succeed on retry.

    Examples: API rate-limits, temporary network timeouts.
    """


class FatalError(Exception):
    """Fatal exception: the application MUST exit immediately.

    Examples: disk full (ENOSPC), out-of-memory (ENOMEM), data corruption.
    """


# ---------------------------------------------------------------------------
# Fatal-pattern detection
# ---------------------------------------------------------------------------

# OSError errno substrings that indicate unrecoverable resource exhaustion.
_FATAL_ERRNO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\[Errno 28\]"),  # ENOSPC – No space left on device
    re.compile(r"\[Errno 12\]"),  # ENOMEM – Cannot allocate memory
]

# Exception types that are always considered fatal.
_FATAL_TYPES: tuple[type[BaseException], ...] = (
    MemoryError,
    SystemExit,
)

# Exception types that are always considered non-fatal.
_NONFATAL_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    ValueError,
    KeyError,
)


def is_fatal_exception(exc: BaseException) -> bool:
    """Determine whether *exc* is fatal and should trigger a forced exit.

    Decision logic (first match wins):
    1. ``asyncio.CancelledError`` is **never** fatal – it is a cooperative
       cancellation signal and must always be re-raised.
    2. Instances of ``_FATAL_TYPES`` (MemoryError, SystemExit) → fatal.
    3. ``OSError`` whose message matches a fatal errno pattern → fatal.
    4. Instances of ``FatalError`` → fatal.
    5. Instances of ``NonFatalError`` (including ``TransientError``) → non-fatal.
    6. Instances of ``_NONFATAL_TYPES`` → non-fatal.
    7. Everything else → **non-fatal** by default (conservative: keep running).
    """
    # CancelledError is cooperative cancellation, not an error.
    if isinstance(exc, asyncio.CancelledError):
        return False

    # Explicit fatal markers.
    if isinstance(exc, _FATAL_TYPES):
        return True

    # OSError with fatal errno.
    if isinstance(exc, OSError):
        msg = str(exc)
        if any(pat.search(msg) for pat in _FATAL_ERRNO_PATTERNS):
            return True

    # Custom hierarchy.
    if isinstance(exc, FatalError):
        return True

    if isinstance(exc, NonFatalError):
        return False

    # Well-known non-fatal built-in types.
    if isinstance(exc, _NONFATAL_TYPES):
        return False

    # Conservative default: assume non-fatal to avoid unnecessary exits.
    return False
