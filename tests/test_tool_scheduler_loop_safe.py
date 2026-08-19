"""ToolScheduler / 模块级 asyncio 原语的事件循环安全测试（2026-08-20）。

背景：Python 3.9 的 asyncio.Lock()/Queue() 构造依赖当前事件循环
（get_event_loop）——asyncio.run() 结束后 set_event_loop(None)（_set_called
残留 True），同进程内再构造 asyncio 原语会抛
``RuntimeError: There is no current event loop``——xdist 全量测试并行下
偶发失败（取决于测试在 worker 内的分配顺序）。

修复（惰性创建）：
  - ToolScheduler._schedule_lock：None + 首次 async 使用 _get_schedule_lock()；
  - search_providers._client_lock / client_async._clients_lock：同上。

覆盖：
  1. asyncio.run 残留态下构造 ToolScheduler 不抛异常（锁未使用前保持 None）；
  2. async 上下文使用锁正常（惰性创建并可用）；
  3. _reset_global_state 后锁重置为 None（下次使用再惰性创建）；
  4. 模块级 asyncio 原语（search_providers / client_async）残留态下
     import 不抛异常、async 使用正常。
"""

from __future__ import annotations

import asyncio

from src.core.tool_executor_async import ToolScheduler


def test_tool_scheduler_construct_after_asyncio_run_no_runtime_error():
    """asyncio.run 残留态下构造 ToolScheduler 不抛异常（修复前 RuntimeError）。"""
    asyncio.run(asyncio.sleep(0))  # 模拟残留态（set_event_loop(None)）
    scheduler = ToolScheduler()
    assert scheduler._schedule_lock is None  # 惰性：未使用前不创建锁


def test_tool_scheduler_lock_lazy_created_in_async_context():
    """_get_schedule_lock 首次 async 使用惰性创建并正常工作。"""
    asyncio.run(asyncio.sleep(0))
    scheduler = ToolScheduler()
    assert scheduler._schedule_lock is None

    async def _use():
        async with scheduler._get_schedule_lock():
            return True

    assert asyncio.run(_use()) is True
    assert scheduler._schedule_lock is not None


def test_tool_scheduler_reset_global_state_resets_lock():
    """_reset_global_state 后锁重置为 None（下次使用再惰性创建）。"""
    asyncio.run(asyncio.sleep(0))
    scheduler = ToolScheduler()

    async def _use():
        async with scheduler._get_schedule_lock():
            return True

    assert asyncio.run(_use()) is True
    assert scheduler._schedule_lock is not None
    scheduler._reset_global_state()
    assert scheduler._schedule_lock is None


def test_module_level_asyncio_locks_lazy_after_asyncio_run():
    """模块级 asyncio 原语惰性化：残留态下 import 不抛异常、async 使用正常。"""
    import src.api.client_async as ca
    import src.tools.search_providers as sp

    asyncio.run(asyncio.sleep(0))
    assert sp._client_lock is None
    assert ca._clients_lock is None

    async def _use_both():
        async with sp._get_client_lock():
            async with ca._get_clients_lock():
                return True

    assert asyncio.run(_use_both()) is True
    assert sp._client_lock is not None
    assert ca._clients_lock is not None
