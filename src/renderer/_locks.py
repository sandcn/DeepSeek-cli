"""低层级锁原语 — 零依赖，无 widget 包依赖。

Layer 0 — 仅依赖标准库 threading/logging/contextlib。
不依赖任何 src/tui/ 包内模块，供需要锁原语但不希望触发
widget 包加载的模块使用（含 renderer 层自身）。

★ 归属说明（2026-07-31 依赖方向解耦）：
  锁原语自 src/tui/_locks.py 迁移至此，消除 renderer→tui 逆向依赖。
  旧 shim（src/tui/_locks.py）已于步骤 8 删除，全部调用方已迁移至真源；
  新代码一律从 src.renderer._locks 导入。

锁体系（与原 widgets/lock.py 一致）：
  render_lock — 保护渲染管线（_drain_queue → _phase_render → _phase_redraw_bottom）
  io_lock     — 保护终端 I/O（LockedTerminal / 裸终端写入）

互斥事件：
  diff_active: diff 渲染标记，ParallelDisplay 刷新循环检查此标记，
  实现 render_diff 与帧刷新的互斥。

输出锁死防护：
  OUTPUT_LOCK_TIMEOUT: render_lock/io_lock 获取的超时阈值（1.0s）。
  防止 PTY 缓冲区满时锁被永久持有，冻结整个输出管线。

★ 锁设计原则（防死锁，必须遵守）：
  1. render_lock 保护渲染管线
  2. io_lock 保护终端 I/O（LockedTerminal）
  3. render_frame 使用非阻塞 try-lock（超时 0.1s），跳过不可获取时的帧
  4. 所有锁获取必须设超时

★ 锁获取顺序规则（防 ABBA 死锁）：
  render_lock → io_lock（render_lock 必须是先获取的锁）
  持有 io_lock 期间禁止再获取 render_lock。

★ 关键路径锁关系图：
  refresh loop:     (try render_lock) → render_frame（I/O）
  _DiffGuard:       _diff_lock → _render_lock（快照）→ _diff_lock → render_lock（I/O）
  print_output:     _render_lock（快照）→ render_lock（I/O）
  TerminalTarget:   仅 io_lock（通过 _try_acquire_output_lock 的 render_lock 降级）
  LockedTerminal:   仅 io_lock
  _BottomBar:       仅 io_lock
  所有锁获取均带超时兜底

★ 导入点审计（2026-07-31 步骤1-子步骤1 差异盘点）：
  迁移后锁导入清单（src/renderer/_locks 新家）：
    - src/renderer/output.py                — render_lock, _try_acquire_output_lock
    - src/tui/_renderer/_engine.py          — _try_acquire_output_lock
    - src/tui/_diff_renderer.py             — diff_active, _try_acquire_output_lock
    - src/tui/events/consumers.py           — _try_acquire_output_lock
    - src/tui/_bottom_bar/_bar.py           — _try_acquire_output_lock
    - src/tui/_bottom_bar/_render.py        — _try_acquire_output_lock
    - src/tui/_lifecycle.py                 — render_lock
    - src/tui/_consumer.py                  — render_lock
    - src/core/adapters/output.py           — render_lock
    - src/tools/file_base.py                — diff_active（已迁移至真源，shim 已删）
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator

# ── 锁实例 ────────────────────────────────────────

render_lock = threading.RLock()     # 渲染管线锁
io_lock = threading.Lock()          # 终端 I/O 锁

diff_active = threading.Event()

# 终端写锁超时阈值（秒）— PTY 缓冲区满时防止锁被长期持有
OUTPUT_LOCK_TIMEOUT = 1.0

_logger = logging.getLogger(__name__)

__all__ = [
    "render_lock",
    "io_lock",
    "diff_active",
    "_try_acquire_output_lock",
    "OUTPUT_LOCK_TIMEOUT",
]


# ── 锁获取上下文管理器 ────────────────────────────


@contextmanager
def _try_acquire_output_lock(
    timeout: float = OUTPUT_LOCK_TIMEOUT,
    name: str = "render",
) -> Generator[bool, None, None]:
    """尝试超时获取 render_lock，超时时 yield False 并降级。

    用于渲染管线（_drain_queue → _phase_render → _phase_redraw_bottom）。

    Args:
        timeout: 等待锁的超时秒数。默认 OUTPUT_LOCK_TIMEOUT (1.0s)
        name: 锁名称标识（仅调试用）

    Yields:
        bool - True 表示成功获取锁，False 表示超时
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
