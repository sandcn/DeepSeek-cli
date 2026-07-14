"""兼容存根 — 重导出到 src.tui.widgets.lock"""

from __future__ import annotations

from src.tui.widgets.lock import (
    render_lock, io_lock, output_lock, diff_active,
    _try_acquire_io_lock, _try_acquire_output_lock,
    locked_print, OUTPUT_LOCK_TIMEOUT,
)
