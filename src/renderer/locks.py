"""低层级锁原语 — 零依赖（renderer 层公共模块）。

Layer 0 — 仅依赖标准库 threading/logging/contextlib 与核心层 ``core.diff_state``。
供需要锁原语但不希望触发 widget 包加载的模块使用（含 renderer 层自身、
表现层 tui、适配器层）。

锁体系：
  render_lock — 保护渲染管线（_drain_queue → _phase_render → _phase_redraw_bottom）
  io_lock     — 保护终端 I/O（LockedTerminal / 裸终端写入）
  diff_active — diff 渲染标记（定义于 core.diff_state，供刷新循环互斥）

输出锁死防护：OUTPUT_LOCK_TIMEOUT 为 render_lock 获取超时阈值（1.0s），
防止 PTY 缓冲区满时锁被永久持有。

★ 锁设计原则（防死锁）：
  1. render_lock 保护渲染管线；2. io_lock 保护终端 I/O；
  3. render_frame 使用非阻塞 try-lock；4. 所有锁获取必须设超时。
★ 锁获取顺序：render_lock → io_lock（持有 io_lock 期间禁止再获取 render_lock）。

旧路径 ``renderer._locks`` 仅 re-export 兼容；新代码从此模块导入。
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator

from ..core.diff_state import diff_active  # noqa: F401  （re-export：diff 互斥标记）

render_lock = threading.RLock()     # 渲染管线锁
io_lock = threading.Lock()          # 终端 I/O 锁

OUTPUT_LOCK_TIMEOUT = 1.0

_logger = logging.getLogger(__name__)

__all__ = [
    "render_lock",
    "io_lock",
    "diff_active",
    "_try_acquire_output_lock",
    "OUTPUT_LOCK_TIMEOUT",
]


@contextmanager
def _try_acquire_output_lock(
    timeout: float = OUTPUT_LOCK_TIMEOUT,
    name: str = "render",
) -> Generator[bool, None, None]:
    """尝试超时获取 render_lock，超时时 yield False 并降级（直写）。

    Yields:
        bool - True 表示成功获取锁，False 表示超时。
    """
    acquired = render_lock.acquire(timeout=timeout)
    if acquired:
        try:
            yield True
        finally:
            render_lock.release()
    else:
        _logger.warning(
            "render_lock 超时（%s, %.1fs），降级为直写",
            name, timeout,
        )
        yield False
