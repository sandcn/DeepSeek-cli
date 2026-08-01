"""test_system_monitor — 零覆盖模块最小测试（方向5 步骤5.5）。

覆盖 ``_SystemMonitor`` 核心路径：get_cpu_and_mem 返回数值、TTL 缓存、
平台读取兜底（异常 → 0.0）、数值钳制 [0, 100]。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui._system_monitor import _SystemMonitor


class TestSystemMonitor:
    """_SystemMonitor CPU/内存采集最小测试。"""

    def test_get_cpu_and_mem_returns_numbers(self):
        """get_cpu_and_mem 返回 (cpu, mem) 数值（mock 底层读取）。"""
        m = _SystemMonitor()
        with patch.object(m, "_read_cpu_proc_stat", return_value=42.0), \
             patch.object(m, "_read_mem_proc_meminfo", return_value=55.0), \
             patch.object(m, "_platform", "linux"), \
             patch.object(m, "_has_psutil", False), \
             patch.object(m, "_last_cpu_time", 0.0), \
             patch.object(m, "_last_mem_time", 0.0):
            cpu, mem = m.get_cpu_and_mem()
        assert cpu == 42.0
        assert mem == 55.0

    def test_get_cpu_percent_clamped(self):
        """CPU 数值钳制到 [0, 100]。"""
        m = _SystemMonitor()
        with patch.object(m, "_read_cpu_proc_stat", return_value=250.0), \
             patch.object(m, "_platform", "linux"), \
             patch.object(m, "_has_psutil", False), \
             patch.object(m, "_last_cpu_time", 0.0):
            v = m.get_cpu_percent()
        assert 0.0 <= v <= 100.0

    def test_get_memory_percent_clamped(self):
        """MEM 数值钳制到 [0, 100]。"""
        m = _SystemMonitor()
        with patch.object(m, "_read_mem_proc_meminfo", return_value=-5.0), \
             patch.object(m, "_platform", "linux"), \
             patch.object(m, "_has_psutil", False), \
             patch.object(m, "_last_mem_time", 0.0):
            v = m.get_memory_percent()
        assert 0.0 <= v <= 100.0

    def test_read_failure_falls_back_to_zero(self):
        """底层读取异常 → 返回 0.0（不抛）。"""
        m = _SystemMonitor()
        with patch.object(m, "_read_cpu_proc_stat", side_effect=OSError("boom")), \
             patch.object(m, "_platform", "linux"), \
             patch.object(m, "_has_psutil", False), \
             patch.object(m, "_last_cpu_time", 0.0):
            v = m.get_cpu_percent()
        assert v == 0.0

    def test_ttl_cache_short_circuits(self):
        """1s TTL 内重复调用不重新读取（缓存返回）。"""
        m = _SystemMonitor()
        m._cpu_percent = 33.0
        m._last_cpu_time = 100.0
        with patch("src.tui._system_monitor.time.monotonic", return_value=100.5), \
             patch.object(m, "_read_cpu_proc_stat") as mock_read:
            v = m.get_cpu_percent()
        assert v == 33.0
        mock_read.assert_not_called()

    def test_detect_platform_returns_known_string(self):
        """_detect_platform 返回已知平台字符串（不抛）。"""
        m = _SystemMonitor()
        assert m._platform in ("linux", "darwin", "windows", "unknown", "cygwin")

    def test_init_no_psutil_no_crash(self):
        """psutil 未安装（ImportError）→ 构造不抛且 _has_psutil=False。"""
        m = _SystemMonitor()
        if m._psutil is None:
            assert m._has_psutil is False
