"""向后兼容存根 — 从 engine/ 重导出。

原模块已迁移至 src/tui/engine/lock.py。
"""

from __future__ import annotations

from ..engine.lock import render_lock, _try_acquire_output_lock

__all__ = ["render_lock", "_try_acquire_output_lock"]
