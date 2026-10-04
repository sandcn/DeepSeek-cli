"""低层级锁原语 — 兼容 re-export 层。

锁原语实现已迁移至公共模块 ``renderer.locks``（消除跨包引用私有模块的
命名不一致）。本模块保留旧路径 ``src.renderer._locks`` 兼容既有调用方；
新代码请使用 ``renderer.locks``。
"""

from __future__ import annotations

from .locks import (  # noqa: F401
    OUTPUT_LOCK_TIMEOUT,
    _try_acquire_output_lock,
    diff_active,
    io_lock,
    render_lock,
)

__all__ = [
    "render_lock",
    "io_lock",
    "diff_active",
    "_try_acquire_output_lock",
    "OUTPUT_LOCK_TIMEOUT",
]
