"""chat_ui 模块的锁薄包装 — 隔离对 ui 层的直接依赖。

当前实现 re-export ui._lock 的全局锁对象，确保语义不变。
未来可迁移为 chat_ui 自有的锁实现。
"""
from ...ui._lock import output_lock, _try_acquire_output_lock

__all__ = ["output_lock", "_try_acquire_output_lock"]
