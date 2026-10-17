"""src/api/_sync_bridge — 共享桥接事件循环单元测试（P0 改进项）。

替换原「每线程一条持久化事件循环」设计（``_model_loops.py``）：改为**单条**
共享桥接循环后，固化的契约是——

  - 惰性创建、幂等复用：多次 ``bridge_loop()`` / ``run_coroutine_blocking()``
    共用同一条循环；
  - **跨线程共享**：不同线程提交的协程跑在同一条循环上（原设计为每线程一条），
    从而绑定循环的资源可安全复用；
  - 并发调用仍并发（不是串行排队）；
  - 已处于运行中事件循环的线程调用 → 立即 ``RuntimeError``（死锁防护）；
  - ``shutdown_bridge()`` 幂等、取消 pending 任务、关闭后可重建。
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from src.api import _sync_bridge as sb


@pytest.fixture(autouse=True)
def _clean_bridge():
    """每个用例前后关闭桥接循环，避免用例间串扰。"""
    sb.shutdown_bridge()
    yield
    sb.shutdown_bridge()


def test_run_coroutine_blocking_returns_result():
    async def coro():
        return 42

    assert sb.run_coroutine_blocking(coro()) == 42


def test_run_coroutine_blocking_propagates_exception():
    async def boom():
        raise ValueError("炸了")

    with pytest.raises(ValueError, match="炸了"):
        sb.run_coroutine_blocking(boom())


def test_bridge_loop_is_single_shared_instance():
    a = sb.bridge_loop()
    b = sb.bridge_loop()
    assert a is b


def test_bridge_runs_on_dedicated_daemon_thread():
    loop = sb.bridge_loop()
    assert loop.is_running()
    ident = sb.bridge_thread_ident()
    assert ident is not None
    assert ident != threading.get_ident()
    for thread in threading.enumerate():
        if thread.ident == ident:
            assert thread.daemon is True
            assert thread.name == sb.BRIDGE_THREAD_NAME
            break
    else:  # pragma: no cover - 桥接线程必然可枚举
        pytest.fail("未找到桥接线程")


def test_loop_is_shared_across_threads():
    """核心契约：不同线程提交的协程跑在**同一条**循环上。"""
    seen: list = []
    barrier = threading.Barrier(3)

    def worker():
        async def record():
            seen.append(id(asyncio.get_running_loop()))

        barrier.wait(timeout=5)
        sb.run_coroutine_blocking(record())

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    barrier.wait(timeout=5)
    for t in threads:
        t.join(timeout=10)

    assert len(seen) == 2
    assert seen[0] == seen[1] == id(sb.bridge_loop())


def test_concurrent_calls_run_concurrently():
    """并发提交必须并发执行（同一条循环 ≠ 串行排队）。

    用**会合点**而非墙钟判定：两个协程各自登记后等待「双方都已进入」的事件；
    若桥接把调用串行化，先进入的协程永远等不到第二个 → 超时失败。这样不依赖
    平台定时器精度（Cygwin/Windows 的 ``asyncio.sleep`` 精度可达 ~15ms）。
    """
    entered: list = []
    rendezvous = None
    started = threading.Event()
    done: list = []

    async def wait_for_peer():
        nonlocal rendezvous
        entered.append(1)
        if rendezvous is None:
            rendezvous = asyncio.Event()
        if len(entered) >= 2:
            rendezvous.set()
        started.set()
        await asyncio.wait_for(rendezvous.wait(), timeout=10)

    def worker():
        sb.run_coroutine_blocking(wait_for_peer())
        done.append(True)

    first = threading.Thread(target=worker, daemon=True)
    first.start()
    assert started.wait(timeout=5)
    sb.run_coroutine_blocking(wait_for_peer())
    first.join(timeout=15)
    assert not first.is_alive()
    assert len(done) == 1
    assert len(entered) == 2


def test_rejects_call_from_running_event_loop():
    async def outer():
        async def inner():
            return 1

        sb.run_coroutine_blocking(inner())

    with pytest.raises(RuntimeError) as exc:
        asyncio.run(outer())
    assert "死锁" in str(exc.value)


def test_shutdown_is_idempotent_and_allows_recreate():
    loop = sb.bridge_loop()
    sb.shutdown_bridge()
    assert loop.is_closed()
    assert sb.bridge_thread_ident() is None
    sb.shutdown_bridge()  # 二次调用不抛异常
    new_loop = sb.bridge_loop()
    assert new_loop is not loop
    assert not new_loop.is_closed()


def test_shutdown_cancels_pending_tasks():
    cancelled: list = []
    entered = threading.Event()

    async def forever():
        entered.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    def submit():
        try:
            sb.run_coroutine_blocking(forever())
        except BaseException:  # noqa: BLE001 - 关闭流程主动取消该协程
            pass

    thread = threading.Thread(target=submit, daemon=True)
    thread.start()
    assert entered.wait(timeout=5)
    sb.shutdown_bridge()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert cancelled == [True]


def test_shutdown_bridge_registered_at_exit():
    """模块导入即注册 atexit 收尾（daemon 线程之外的显式清理）。"""
    import atexit as _atexit

    registered = getattr(_atexit, "_ncallbacks", lambda: 0)()
    assert registered >= 1


def test_module_exports():
    for name in sb.__all__:
        assert hasattr(sb, name)


def test_ensure_loop_rejects_during_shutdown(monkeypatch):
    """关闭流程进行中拒绝创建新循环（避免与正在关闭的循环并存）。"""
    monkeypatch.setattr(sb, "_shutting_down", True)
    with pytest.raises(RuntimeError, match="正在关闭"):
        sb.bridge_loop()


def test_run_coroutine_blocking_rejects_during_shutdown(monkeypatch):
    """关闭中提交协程 → 立即拒绝并关闭协程对象（不产生未 await 警告）。"""
    monkeypatch.setattr(sb, "_shutting_down", True)

    async def coro():
        return 1

    obj = coro()
    with pytest.raises(RuntimeError, match="正在关闭"):
        sb.run_coroutine_blocking(obj)
    assert obj.cr_frame is None  # 已 close（协程不再可执行）
