"""后台任务强引用注册表 — 防止 fire-and-forget 任务被 GC 回收。

CPython 的事件循环只以**弱引用**持有 Task（``asyncio.all_tasks`` 即依赖它），
没有任何强引用的任务在事件循环空闲时可能被垃圾回收：任务静默消失、取消
请求丢失、且不会抛任何异常（只在极端情况留下 "Task was destroyed but it is
pending"）。

本模块提供显式跟踪（供信号回调、内核事件 emit、延迟自检等 fire-and-forget
场景使用）：

    task = spawn_tracked(loop, coro)   # 调度并保存强引用
    track_task(task)                   # 已有任务补登记
    tracked_count()                    # 当前受管任务数（自省/测试）
    release_all()                      # 丢弃全部引用（不取消任务）

任务结束（含取消）后经 ``add_done_callback`` 自动移出注册表，不泄漏。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Optional, Set

_logger = logging.getLogger(__name__)

#: 受管后台任务集合（强引用）；任务结束时自动移除。
_tracked_tasks: Set["asyncio.Task"] = set()
_lock = threading.Lock()


def _on_task_done(task: "asyncio.Task") -> None:
    """任务结束回调：移出注册表；非取消异常记 debug（避免「异常从未读取」）。"""
    with _lock:
        _tracked_tasks.discard(task)
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except (asyncio.CancelledError, asyncio.InvalidStateError):  # pragma: no cover
        return
    if exc is not None:
        _logger.debug("受管后台任务异常: %r", exc, exc_info=exc)


def track_task(task: Optional["asyncio.Task"]) -> Optional["asyncio.Task"]:
    """登记一个已创建的任务（保存强引用，结束时自动移除）。"""
    if task is None:
        return None
    with _lock:
        _tracked_tasks.add(task)
    try:
        task.add_done_callback(_on_task_done)
    except Exception:  # pragma: no cover - 防御：回调注册失败不影响任务本身
        _logger.debug("登记后台任务失败", exc_info=True)
    return task


def spawn_tracked(loop: Any, coro: Any, *, name: str = "") -> Optional["asyncio.Task"]:
    """在 ``loop`` 上调度协程并保存强引用；loop 不可用时关闭协程并返回 None。

    Args:
        loop: 目标事件循环（``None`` 视为不可用）。
        coro: 待调度的协程对象。
        name: 可选任务名（``Task.set_name``，便于调试）。
    """
    if loop is None:
        _close_quietly(coro)
        return None
    try:
        task = loop.create_task(coro)
    except RuntimeError:  # pragma: no cover - loop 已关闭/不可调度
        _logger.debug("调度后台任务失败（loop 不可用）", exc_info=True)
        _close_quietly(coro)
        return None
    if name:
        try:
            task.set_name(name)
        except (AttributeError, ValueError):  # pragma: no cover - 老版本/非法名
            pass
    return track_task(task)


def _close_quietly(coro: Any) -> None:
    """未能调度时关闭协程（避免 "coroutine was never awaited" 告警）。"""
    close = getattr(coro, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # pragma: no cover
            pass


def tracked_count() -> int:
    """当前受管任务数（测试/自省用）。"""
    with _lock:
        return len(_tracked_tasks)


def release_all() -> None:
    """丢弃全部跟踪引用（不取消任务；供退出/测试清理使用）。"""
    with _lock:
        _tracked_tasks.clear()


__all__ = ["track_task", "spawn_tracked", "tracked_count", "release_all"]
