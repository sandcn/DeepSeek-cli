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


def cleanup_model_loops() -> None:
    """关闭所有持久化事件循环。

    使用 asyncio.all_tasks() + loop.stop() 代替直接 close()，
    避免与 asyncio.run() 的事件循环清理竞争。
    在各持久化事件循环中安全停止所有任务再关闭。
    """
    with _model_loop_registry_lock:
        for tid, loop in list(_model_loop_registry.items()):
            try:
                if loop.is_closed():
                    continue
                # 先停止所有 pending 任务
                try:
                    pending = asyncio.all_tasks(loop=loop)
                    for task in pending:
                        task.cancel()
                    if not loop.is_closed():
                        loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
                # 尝试关闭
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
