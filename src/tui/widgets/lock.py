"""
UI 层输出同步锁 — render_lock（渲染锁）

本模块从 src/tui/_locks.py 导入并 re-export 锁原语，
保持与 widgets.lock 现有 import 路径的兼容性。

锁体系说明见 _locks.py。
"""

# 从零依赖层级模块导入锁原语
from .._locks import (
    render_lock,
    io_lock,
    diff_active,
    OUTPUT_LOCK_TIMEOUT,
    _try_acquire_output_lock,
)
