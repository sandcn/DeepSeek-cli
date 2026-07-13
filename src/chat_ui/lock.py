"""chat_ui 模块的锁薄包装 — 隔离对 ui 层的直接依赖。

当前实现 re-export ui._lock 的全局锁对象，确保语义不变。
未来可迁移为 chat_ui 自有的锁实现。

导出锁清单：
  - render_lock: 渲染管线锁（_drain_queue → _phase_render → _phase_redraw_bottom）
  - io_lock:     终端 I/O 锁（locked_print / LockedTerminal）
  - output_lock: @deprecated render_lock 的兼容别名，v1.3+ 将移除
"""
from ..ui._lock import (
    output_lock,
    render_lock,
    io_lock,
    _try_acquire_output_lock,
)

__all__ = [
    "output_lock",
    "render_lock",
    "io_lock",
    "_try_acquire_output_lock",
]
