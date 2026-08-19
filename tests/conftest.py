"""pytest 全局夹具。

★ 2026-08-20（全量测试稳定性）：确保每个测试开始前主线程存在可用事件循环。
Python 3.9 的 asyncio.run() 结束后会 set_event_loop(None)（_set_called 残留
True），同进程内再构造 asyncio 原语（Queue/Lock/Event 等）会经
get_event_loop() 抛 ``RuntimeError: There is no current event loop``——
xdist 多 worker 并行下偶发失败（取决于测试在 worker 内的分配顺序）。
本夹具在每个测试前修复该状态，消除整类偶发失败。
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_main_event_loop():
    """主线程事件循环兜底：无循环时（asyncio.run 残留态）重建一个。"""
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield
