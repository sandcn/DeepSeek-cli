"""系统监控：帧内同步采集，不再持有独立后台线程。"""

from __future__ import annotations

from src.tui._system_monitor import _SystemMonitor


def test_no_background_thread_and_sync_collect() -> None:
    monitor = _SystemMonitor()
    calls: list[str] = []

    monitor.get_cpu_percent = lambda: calls.append("cpu") or 1.0  # type: ignore[assignment]
    monitor.get_memory_percent = lambda: calls.append("mem") or 2.0  # type: ignore[assignment]

    cpu, mem = monitor.get_cpu_and_mem()

    assert calls == ["cpu", "mem"]
    assert isinstance(cpu, float) and isinstance(mem, float)
    assert not hasattr(monitor, "_bg_thread")
    assert not hasattr(monitor, "_bg_loop")

    # stop 保留兼容语义（幂等 no-op）
    monitor.stop()
    monitor.stop()
