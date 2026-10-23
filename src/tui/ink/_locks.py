"""输出锁原语 — 框架侧默认实现（应用可注入自己的渲染锁）。

``InkSession`` 在 suspend/stop 等跨线程写入点需要与「渲染管线锁」互斥：
应用侧的真源是 ``src/renderer/locks.py`` 的 ``_try_acquire_output_lock``
（1 秒超时、超时降级直写并记 warning），框架历史上直接 import 它，形成
「框架 → renderer 层」的反向依赖。

本模块给出**同签名**的默认实现（进程级 RLock、总能获取、yield True），
保证「没有注入时行为安全」；生产装配注入应用实现后语义完全不变。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Generator

__all__ = ["default_output_lock", "DEFAULT_OUTPUT_LOCK_TIMEOUT"]

DEFAULT_OUTPUT_LOCK_TIMEOUT: float = 1.0

_output_lock = threading.RLock()


@contextmanager
def default_output_lock(
    timeout: float = DEFAULT_OUTPUT_LOCK_TIMEOUT,
    name: str = "render",
) -> Generator[bool, None, None]:
    """获取框架默认输出锁（RLock；不可重入场景同样安全）。

    Args:
        timeout: 超时秒数（本实现总会成功，参数保留以兼容应用实现签名）。
        name: 诊断名（本实现不使用）。

    Yields:
        True（始终成功获取）。
    """
    acquired = _output_lock.acquire(timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            _output_lock.release()
