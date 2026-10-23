"""系统监控：后台线程采集，帧内只读缓存（2026-10-23 渲染线程阻塞修复）。

原实现（2026-10-23 game_loop）在渲染帧 SYSTEM_STATS 阶段同步采集——
无 psutil 平台的子进程采样（iostat/powershell/sysctl，timeout 最长 5s）
会阻塞 60Hz 渲染线程与输入处理。现改为后台线程采集 + 帧内只读缓存。
"""

from __future__ import annotations

import time

from src.tui._system_monitor import _SystemMonitor


def test_frame_read_is_non_blocking_and_background_collects() -> None:
    monitor = _SystemMonitor()
    calls: list[str] = []

    monitor.get_cpu_percent = lambda: calls.append("cpu") or 1.0  # type: ignore[assignment]
    monitor.get_memory_percent = lambda: calls.append("mem") or 2.0  # type: ignore[assignment]

    # 未启动后台线程：帧内读取只返回缓存，不触发采集
    assert monitor.get_cpu_and_mem() == (0.0, 0.0)
    assert calls == []

    monitor.start(0.02)
    try:
        deadline = time.monotonic() + 2.0
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "cpu" in calls and "mem" in calls
        assert isinstance(monitor.get_cpu_and_mem()[0], float)
    finally:
        monitor.stop()

    assert monitor._thread is None
    # stop 幂等
    monitor.stop()
    monitor.stop()
