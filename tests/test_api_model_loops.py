"""src/api/_model_loops — 持久化事件循环管理单元测试。

覆盖：
  - _get_model_loop 每线程独立、复用、关闭后自动重建
  - _register_model_loop / cleanup_model_loops（全量关闭、幂等、空 registry）
  - atexit 注册存在性
"""

from __future__ import annotations

import asyncio
import threading
import warnings

import pytest

import src.api._model_loops as ml


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    """每个测试前后清理 thread-local 与全局 registry。"""
    saved = dict(ml._model_loop_registry)
    try:
        # 关闭测试期间可能遗留的 loop，避免资源泄漏
        with ml._model_loop_registry_lock:
            for loop in list(ml._model_loop_registry.values()):
                try:
                    if not loop.is_closed():
                        loop.close()
                except RuntimeError:
                    pass
            ml._model_loop_registry.clear()
        ml._model_loops.__dict__.clear()
        yield
    finally:
        with ml._model_loop_registry_lock:
            ml._model_loop_registry.clear()
        ml._model_loops.__dict__.clear()
        # 恢复原 registry（不关闭，交由原主）
        for _tid, loop in saved.items():
            with ml._model_loop_registry_lock:
                ml._model_loop_registry[_tid] = loop


def test_get_model_loop_creates_and_registers():
    loop = ml._get_model_loop()
    assert isinstance(loop, asyncio.AbstractEventLoop)
    assert ml._model_loop_registry[threading.get_ident()] is loop


def test_get_model_loop_reuses_same_loop():
    a = ml._get_model_loop()
    b = ml._get_model_loop()
    assert a is b


def test_get_model_loop_rebuilds_after_close():
    loop = ml._get_model_loop()
    tid = threading.get_ident()
    loop.close()
    # 关闭后 registry 仍指向旧 loop，但 _get_model_loop 应检测 is_closed 重建
    new_loop = ml._get_model_loop()
    assert new_loop is not loop
    assert not new_loop.is_closed()
    assert ml._model_loop_registry[tid] is new_loop


def test_get_model_loop_per_thread_independent():
    results = {}
    barrier = threading.Barrier(2)

    def worker():
        loop = ml._get_model_loop()
        results[threading.get_ident()] = loop
        barrier.wait()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    loops = list(results.values())
    assert len(set(loops)) == 2  # 两个线程各自独立 loop


def test_cleanup_model_loops_closes_all():
    loop = ml._get_model_loop()
    assert not loop.is_closed()
    ml.cleanup_model_loops()
    assert loop.is_closed()
    assert ml._model_loop_registry == {}


def test_cleanup_model_loops_idempotent_empty():
    ml.cleanup_model_loops()
    ml.cleanup_model_loops()  # 空 registry 二次调用不抛异常


def test_cleanup_model_loops_skips_already_closed():
    loop = ml._get_model_loop()
    loop.close()
    ml.cleanup_model_loops()  # is_closed 分支，不抛异常
    assert ml._model_loop_registry == {}


def test_cleanup_model_loops_cancels_pending_tasks():
    loop = ml._get_model_loop()

    async def _never():
        await asyncio.sleep(3600)

    task = loop.create_task(_never())
    task._log_destroy_pending = False  # 抑制 loop 关闭时的 pending 告警
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ml.cleanup_model_loops()
    # ★ 修复后语义：pending 任务被 cancel 并**驱动至结束**（不再只置
    #   ``_must_cancel`` 后被 loop.close() 直接丢弃），任务处于 cancelled 终态。
    assert task.done() is True
    assert task.cancelled() is True
    # 且不再产生「协程从未被 await」告警（旧实现直接 close 会丢弃协程）。
    assert not any("never awaited" in str(w.message) for w in caught)


def test_cleanup_model_loops_drives_started_task_to_finish():
    """已启动（挂起中）的 pending 任务同样被取消并驱动到结束。"""
    loop = ml._get_model_loop()
    events: list[str] = []

    async def _work():
        events.append("started")
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    task = loop.create_task(_work())
    task._log_destroy_pending = False
    loop.run_until_complete(asyncio.sleep(0))  # 驱动任务进入挂起点
    assert events == ["started"]

    ml.cleanup_model_loops()

    assert task.cancelled() is True
    # CancelledError 真正送达协程（证明循环被驱动，而非 close 丢弃任务）
    assert events == ["started", "cancelled"]


def test_atexit_registered():
    """cleanup_model_loops 已注册到 atexit（模块导入后回调计数 >= 1）。"""
    assert ml.atexit._ncallbacks() >= 1  # type: ignore[attr-defined]
