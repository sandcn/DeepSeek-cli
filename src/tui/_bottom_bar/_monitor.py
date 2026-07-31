"""系统监控模块 — _SystemMonitor 跨平台 CPU/内存采集。

从 ``_bottom_bar.py`` 提取为独立子模块。
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import sys
import time
from typing import Any

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# _SystemMonitor — 跨平台系统监控
# ═══════════════════════════════════════════════════════════

class _SystemMonitor:
    """跨平台系统监控 — CPU 与内存使用率采集。

    惰性初始化：仅在首次采集时才检测平台和尝试导入 psutil。
    使用 1 秒缓存消峰。
    """

    CPU_CACHE_TTL: float = 1.0
    MEM_CACHE_TTL: float = 1.0

    def __init__(self) -> None:
        self._platform: str = self._detect_platform()
        self._psutil: Any = None
        self._has_psutil: bool = False
        self._try_init_psutil()
        self._cpu_percent: float = 0.0
        self._last_cpu_time: float = 0.0
        self._prev_cpu_total: int = 0
        self._prev_cpu_idle: int = 0
        self._have_prev_cpu: bool = False
        self._mem_percent: float = 0.0
        self._last_mem_time: float = 0.0

    @staticmethod
    def _detect_platform() -> str:
        raw = platform.system().lower()
        if "cygwin" in raw or (sys.platform and "cygwin" in sys.platform):
            return "cygwin"
        if raw == "linux":
            return "linux"
        if raw == "darwin":
            return "darwin"
        if raw == "windows":
            return "windows"
        return "unknown"

    def _try_init_psutil(self) -> None:
        try:
            import psutil
            self._psutil = psutil
            self._has_psutil = True
        except ImportError:
            self._psutil = None
            self._has_psutil = False

    def get_cpu_percent(self) -> float:
        now = time.monotonic()
        if now - self._last_cpu_time < self.CPU_CACHE_TTL:
            return self._cpu_percent
        self._last_cpu_time = now
        try:
            if self._has_psutil:
                self._cpu_percent = float(self._psutil.cpu_percent(interval=0))
            elif self._platform in ("linux", "cygwin"):
                self._cpu_percent = self._read_cpu_proc_stat()
            elif self._platform == "darwin":
                self._cpu_percent = self._read_cpu_macos()
            elif self._platform == "windows":
                self._cpu_percent = self._read_cpu_windows()
            else:
                self._cpu_percent = 0.0
        except Exception:
            _logger.warning("Failed to read CPU usage", exc_info=True)
            self._cpu_percent = 0.0
        self._cpu_percent = max(0.0, min(100.0, self._cpu_percent))
        return self._cpu_percent

    def get_memory_percent(self) -> float:
        now = time.monotonic()
        if now - self._last_mem_time < self.MEM_CACHE_TTL:
            return self._mem_percent
        self._last_mem_time = now
        try:
            if self._has_psutil:
                self._mem_percent = float(self._psutil.virtual_memory().percent)
            elif self._platform in ("linux", "cygwin"):
                self._mem_percent = self._read_mem_proc_meminfo()
            elif self._platform == "darwin":
                self._mem_percent = self._read_mem_macos()
            elif self._platform == "windows":
                self._mem_percent = self._read_mem_windows()
            else:
                self._mem_percent = 0.0
        except Exception:
            _logger.warning("Failed to read memory usage", exc_info=True)
            self._mem_percent = 0.0
        self._mem_percent = max(0.0, min(100.0, self._mem_percent))
        return self._mem_percent

    def get_cpu_and_mem(self) -> tuple[float, float]:
        self.get_cpu_percent()
        self.get_memory_percent()
        return (self._cpu_percent, self._mem_percent)

    def _read_cpu_proc_stat(self) -> float:
        try:
            with open("/proc/stat") as f:
                line = f.readline()
        except (OSError, IOError):
            return 0.0
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return 0.0
        values = []
        for v in parts[1:]:
            try:
                values.append(int(v))
            except (ValueError, IndexError):
                break
        if len(values) < 4:
            return 0.0
        total = sum(values)
        idle = values[3]
        if not self._have_prev_cpu:
            self._prev_cpu_total = total
            self._prev_cpu_idle = idle
            self._have_prev_cpu = True
            return 0.0
        delta_total = total - self._prev_cpu_total
        delta_idle = idle - self._prev_cpu_idle
        self._prev_cpu_total = total
        self._prev_cpu_idle = idle
        if delta_total <= 0:
            return 0.0
        return 100.0 * (1.0 - delta_idle / delta_total)

    def _read_cpu_macos(self) -> float:
        try:
            result = subprocess.run(
                ["iostat", "-c", "2", "2"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 4:
                parts = lines[-1].split()
                if len(parts) >= 6:
                    idle_str = parts[-1].replace("%", "")
                    return max(0.0, 100.0 - float(idle_str))
            return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: iostat -c 2 2", exc_info=True)
            return 0.0
        except (OSError, ValueError, IndexError):
            return 0.0

    def _read_cpu_windows(self) -> float:
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage", "/format:value"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.startswith("LoadPercentage="):
                    return float(line.split("=", 1)[1].strip())
            return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: wmic cpu get loadpercentage", exc_info=True)
            return 0.0
        except (OSError, ValueError, IndexError):
            return 0.0

    def _read_mem_proc_meminfo(self) -> float:
        meminfo: dict[str, int] = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    key, rest = line.split(":", 1)
                    key = key.strip()
                    val_str = rest.strip().split()[0]
                    try:
                        meminfo[key] = int(val_str)
                    except (ValueError, IndexError):
                        continue
        except (OSError, IOError):
            return 0.0
        total = meminfo.get("MemTotal", 0)
        if total <= 0:
            return 0.0
        available = meminfo.get("MemAvailable")
        if available is not None and available > 0:
            used = total - available
            return 100.0 * used / total
        free = meminfo.get("MemFree", 0)
        cached = meminfo.get("Cached", 0)
        buffers = meminfo.get("Buffers", 0)
        used = total - free - cached - buffers
        return 100.0 * max(0, used) / total

    def _read_mem_macos(self) -> float:
        total_bytes = 0
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3.0,
            )
            if result.returncode == 0:
                total_bytes = int(result.stdout.strip())
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: sysctl -n hw.memsize", exc_info=True)
            return 0.0
        except (OSError, ValueError):
            return 0.0
        if total_bytes <= 0:
            return 0.0
        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True, text=True, timeout=3.0,
            )
            if result.returncode != 0:
                return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: vm_stat", exc_info=True)
            return 0.0
        except OSError:
            return 0.0
        page_size = 4096
        active_pages = 0
        wired_pages = 0
        compressed_pages = 0
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if "page size of" in line:
                m = re.search(r"page size of (\d+)", line)
                if m:
                    page_size = int(m.group(1))
            elif line.startswith("Pages active:"):
                try:
                    active_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
            elif line.startswith("Pages wired down:"):
                try:
                    wired_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
            elif line.startswith("Pages stored in compressor:"):
                try:
                    compressed_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
        used_bytes = (active_pages + wired_pages + compressed_pages) * page_size
        return 100.0 * used_bytes / total_bytes

    def _read_mem_windows(self) -> float:
        try:
            result = subprocess.run(
                [
                    "wmic", "OS", "get",
                    "TotalVisibleMemorySize,FreePhysicalMemory",
                    "/format:value",
                ],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: wmic OS get TotalVisibleMemorySize,FreePhysicalMemory", exc_info=True)
            return 0.0
        except OSError:
            return 0.0
        total_kb = 0
        free_kb = 0
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("TotalVisibleMemorySize="):
                try:
                    total_kb = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
            elif line.startswith("FreePhysicalMemory="):
                try:
                    free_kb = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
        if total_kb <= 0:
            return 0.0
        return 100.0 * (total_kb - free_kb) / total_kb


__all__ = ["_SystemMonitor"]
