"""全局中断信号 — 兼容 re-export 层。

实现已下沉核心层 ``core.interrupt_state``（消除核心层对 api 的反向依赖）。
本模块保留旧路径 ``src.api.interrupt_async`` 兼容既有调用方；新代码请使用
``src.core.interrupt_state``。
"""

from __future__ import annotations

from ..core.interrupt_state import (  # noqa: F401
    _flush_stdin,
    flush_stdin,
    is_interrupted,
    is_interrupted_async,
    is_kill_background_requested,
    request_interrupt_async,
    request_kill_background,
    reset_interrupt_async,
    reset_kill_background,
    wait_for_interrupt_async,
)

__all__ = [
    "is_interrupted_async",
    "request_interrupt_async",
    "flush_stdin",
    "reset_interrupt_async",
    "is_interrupted",
    "wait_for_interrupt_async",
    "request_kill_background",
    "is_kill_background_requested",
    "reset_kill_background",
    "_flush_stdin",
]
