"""系统监控后台采集线程（2026-10-23 渲染线程阻塞修复）回归测试。"""

from __future__ import annotations

import time

from src.tui._system_monitor import _SystemMonitor


def test_background_thread_collects_and_stops(monkeypatch) -> None:
    monitor = _SystemMonitor()
    calls: list[str] = []
    monkeypatch.setattr(
        monitor, "get_cpu_percent", lambda: calls.append("cpu") or 12.0,
    )
    monkeypatch.setattr(
        monitor, "get_memory_percent", lambda: calls.append("mem") or 34.0,
    )
    monitor.start(0.02)
    deadline = time.monotonic() + 2.0
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls
    monitor.stop()
    assert monitor._thread is None


def test_get_cpu_and_mem_does_not_block_or_collect(monkeypatch) -> None:
    monitor = _SystemMonitor()
    calls: list[str] = []
    monkeypatch.setattr(monitor, "get_cpu_percent", lambda: calls.append("cpu") or 1.0)
    monkeypatch.setattr(monitor, "get_memory_percent", lambda: calls.append("mem") or 2.0)
    # 未启动后台线程：读取缓存不触发采集
    assert monitor.get_cpu_and_mem() == (0.0, 0.0)
    assert calls == []


def test_start_is_idempotent(monkeypatch) -> None:
    monitor = _SystemMonitor()
    monkeypatch.setattr(monitor, "get_cpu_percent", lambda: 1.0)
    monkeypatch.setattr(monitor, "get_memory_percent", lambda: 1.0)
    monitor.start(0.05)
    first = monitor._thread
    monitor.start(0.05)
    assert monitor._thread is first
    monitor.stop()
