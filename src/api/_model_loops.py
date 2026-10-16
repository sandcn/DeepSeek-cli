"""事件循环管理 — 持久化事件循环的创建、注册与清理

从 model_async.py 提取，独立管理持久化事件循环的生命周期。
每个调用线程持有独立循环（threading.local），互不干扰。

供 model_async.py 中的 call_model_sync / call_model 同步兼容包装使用。
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import threading

_logger = logging.getLogger(__name__)

# 每个线程独立的事件循环实例（threading.local）
_model_loops = threading.local()
# 全局注册表：线程 ID → 事件循环
_model_loop_registry: dict[int, asyncio.AbstractEventLoop] = {}
_model_loop_registry_lock = threading.Lock()


def _get_model_loop() -> asyncio.AbstractEventLoop:
    """获取当前线程的持久化事件循环。

    每个线程首次调用时创建新循环并注册到全局 registry，
    后续调用复用已有循环（除非已关闭，则自动重建）。
    """
    try:
        loop = _model_loops.loop
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            _model_loops.loop = loop
            _register_model_loop(loop)
        return loop
    except AttributeError:
        loop = asyncio.new_event_loop()
        _model_loops.loop = loop
        _register_model_loop(loop)
        return loop


def _register_model_loop(loop: asyncio.AbstractEventLoop) -> None:
    """将事件循环注册到全局 registry，供 cleanup 遍历关闭。"""
    with _model_loop_registry_lock:
        _model_loop_registry[threading.get_ident()] = loop


def _drain_pending_tasks(loop: asyncio.AbstractEventLoop) -> int:
    """取消并驱动 loop 上的 pending 任务直至结束，返回被取消的任务数。

    ★ 必须驱动：未运行的 loop 直接 ``close()`` 会**丢弃** pending 任务
    （task 被销毁、协程从未 await、取消请求不生效），表现为
    ``Task was destroyed but it is pending`` 与
    ``coroutine ... was never awaited``。运行中的 loop（另一线程持有）
    只发取消，由其线程推进。
    """
    try:
        pending = [task for task in asyncio.all_tasks(loop=loop) if not task.done()]
    except RuntimeError:
        return 0
    if not pending:
        return 0
    for task in pending:
        task.cancel()
    if loop.is_running():
        return len(pending)
    try:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except (RuntimeError, asyncio.CancelledError):
        _logger.debug("cleanup_model_loops 等待任务取消完成失败", exc_info=True)
    return len(pending)


def cleanup_model_loops() -> None:
    """关闭所有持久化事件循环。

    顺序：取消 pending 任务 → （非运行 loop）驱动至取消完成 → 关闭。
    运行中的 loop 只请求停止（``call_soon_threadsafe(loop.stop)``），
    由其持有线程自行结束——避免与 ``asyncio.run()`` 的清理竞争。
    """
    with _model_loop_registry_lock:
        for _, loop in list(_model_loop_registry.items()):
            try:
                if loop.is_closed():
                    continue
                _drain_pending_tasks(loop)
                if loop.is_running():
                    try:
                        loop.call_soon_threadsafe(loop.stop)
                    except RuntimeError:
                        pass
                else:
                    try:
                        loop.close()
                    except RuntimeError:
                        pass
            except Exception:
                _logger.debug(
                    "cleanup_model_loops 关闭 loop 异常", exc_info=True,
                )
        _model_loop_registry.clear()


atexit.register(cleanup_model_loops)
