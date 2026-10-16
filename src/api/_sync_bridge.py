"""同步桥接 — 把「同步调用异步代码」收敛到**单条**共享后台事件循环。

背景（P0 改进项）：历史上每个调用线程各自持有一条**持久化事件循环**
（``threading.local`` + 全局注册表 + ``atexit`` 遍历清理）。该设计带来三类问题：

1. **循环数量随线程增长** —— 每个 worker 线程一条循环，绑定循环的资源
   （如 ``httpx.AsyncClient``）随之复制；跨循环复用即报
   ``bound to a different event loop``（这正是当初引入持久化循环要规避、却
   反过来制造的问题）。
2. **退出路径复杂** —— 需要在 ``atexit`` 里遍历注册表、逐条取消 pending 任务、
   区分「运行中/未运行」循环分别处理。
3. **归属不可回答** —— 「这次请求到底跑在哪条循环上」无从回答，事故定位困难。

本模块改为**单条共享桥接循环**（独立 daemon 线程持有）：所有同步包装统一经
``run_coroutine_blocking`` 提交协程并等待结果。

- 循环恒为 1 条 → 绑定循环的资源可安全跨调用复用，且并发调用**仍然并发**
  （协程在桥接循环上并发执行，不是串行排队）；
- 退出只需关闭一条循环，pending 任务在关闭前被取消并驱动至结束；
- 在**已运行事件循环**的线程上调用会立即抛出可读错误，而不是抛出
  ``This event loop is already running`` 这类难以定位的异常。

> 注意：同步包装仍是「阻塞调用线程」的语义——它面向**本来就在工作线程里运行**
> 的同步调用方（如经 ``asyncio.to_thread`` 调度的压缩流程）。异步上下文应直接
> 使用 ``call_model_async`` / ``call_model_sync_async`` /
> ``call_model_summarize_async``，本模块会在这种情况下明确拒绝执行。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading

_logger = logging.getLogger(__name__)

#: 桥接循环线程名（诊断 / 测试断言用）
BRIDGE_THREAD_NAME = "chat-sync-bridge"

#: 关闭桥接循环时等待 pending 任务收尾的上限（秒）
_DRAIN_TIMEOUT = 5.0

#: 关闭线程 join 上限（秒）
_JOIN_TIMEOUT = 2.0

_loop: "asyncio.AbstractEventLoop | None" = None
_thread: "threading.Thread | None" = None
_lock = threading.Lock()


def _serve(loop: asyncio.AbstractEventLoop) -> None:
    """桥接线程主体：把 loop 设为该线程的事件循环并常驻运行。"""
    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    except Exception:  # pragma: no cover - 循环异常退出属环境级故障
        _logger.debug("桥接循环异常退出", exc_info=True)
    finally:
        asyncio.set_event_loop(None)


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """惰性创建共享桥接循环（幂等、线程安全；循环已关闭时自动重建）。"""
    global _loop, _thread
    with _lock:
        if (
            _loop is not None
            and not _loop.is_closed()
            and _thread is not None
            and _thread.is_alive()
        ):
            return _loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=_serve, args=(loop,), name=BRIDGE_THREAD_NAME, daemon=True,
        )
        thread.start()
        _loop, _thread = loop, thread
        return loop


def bridge_loop() -> asyncio.AbstractEventLoop:
    """当前共享桥接循环（首次调用时创建）。"""
    return _ensure_loop()


def bridge_thread_ident() -> "int | None":
    """桥接线程 ident（未创建时 None）——供测试/诊断判断当前线程身份。"""
    thread = _thread
    return None if thread is None else thread.ident


def run_coroutine_blocking(coro):
    """在共享桥接循环上运行 ``coro``，并阻塞当前线程等待结果。

    Raises:
        RuntimeError: 当前线程已处于运行中的事件循环——此时阻塞等待必然死锁，
            直接失败并给出可读提示（替代历史实现里 ``run_until_complete`` 抛出
            的 ``This event loop is already running``）。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass  # 无运行中的循环 → 可以安全阻塞
    else:
        # 拒绝前先关闭传入的协程对象：它不会被任何循环执行，否则解释器会在
        # 垃圾回收时抛 "coroutine ... was never awaited" 警告噪音。
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "同步模型包装不能在已运行事件循环的线程上调用（阻塞等待会死锁）；"
            "请改用 async 接口：call_model_async / call_model_sync_async / "
            "call_model_summarize_async"
        )
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


async def _drain_tasks() -> int:
    """取消桥接循环上除自身外的全部 pending 任务并等待收尾，返回取消数。"""
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    return len(pending)


def shutdown_bridge() -> None:
    """关闭共享桥接循环（幂等）。

    顺序：取消并等待 pending 任务 → 停止循环 → join 桥接线程 → 关闭循环。
    停止前先驱动取消，避免出现 ``Task was destroyed but it is pending`` 与
    「协程从未被 await」的退出噪音。
    """
    global _loop, _thread
    with _lock:
        loop, thread = _loop, _thread
        _loop, _thread = None, None
    if loop is None:
        return
    if not loop.is_closed() and thread is not None and thread.is_alive():
        try:
            asyncio.run_coroutine_threadsafe(_drain_tasks(), loop).result(
                timeout=_DRAIN_TIMEOUT,
            )
        except Exception:
            _logger.debug("桥接循环 pending 任务收尾失败", exc_info=True)
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            _logger.debug("桥接循环已停止，无需再停", exc_info=True)
    if thread is not None and thread.is_alive():
        thread.join(timeout=_JOIN_TIMEOUT)
    try:
        if not loop.is_closed():
            loop.close()
    except Exception:  # pragma: no cover - 关闭异常不应影响退出
        _logger.debug("关闭桥接循环异常", exc_info=True)


#: 进程退出兜底：daemon 线程不会阻止退出，但仍主动收尾 pending 任务。
atexit.register(shutdown_bridge)


__all__ = [
    "BRIDGE_THREAD_NAME",
    "bridge_loop",
    "bridge_thread_ident",
    "run_coroutine_blocking",
    "shutdown_bridge",
]
