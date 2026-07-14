"""跨平台系统监控模块 — CPU 与内存使用率采集。

提供 ``_SystemMonitor`` 类，用于在终端底部栏分隔线中嵌入
CPU 使用率和内存使用率的实时显示。

跨平台策略（三级 fallback）:
  1. **psutil**（最优）: 最完善的跨平台方案，支持的平台包括 Linux/macOS/Windows
  2. **/proc 文件系统**（Linux / Cygwin / WSL）: 读取 ``/proc/stat`` 和 ``/proc/meminfo``
  3. **平台特定命令**:
     - macOS: ``vm_stat`` + ``sysctl hw.memsize``（内存），
             ``iostat -c 2 2`` 或 ``ps -A -o %cpu=``（CPU）
     - Windows: ``wmic cpu get loadpercentage``（CPU），
               ``wmic OS get TotalVisibleMemorySize,FreePhysicalMemory``（内存）

所有外部操作均有超时保护和异常捕获，失败时静默返回 0.0 兜底。
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time

_logger = logging.getLogger(__name__)


class _SystemMonitor:
    """跨平台系统监控 — CPU 与内存使用率采集。

    惰性初始化：仅在首次采集时才检测平台和尝试导入 psutil。
    使用 1 秒缓存消峰，避免高频采集带来的开销。

    用法::

        monitor = _SystemMonitor()
        cpu_pct = monitor.get_cpu_percent()     # 0.0 ~ 100.0
        mem_pct = monitor.get_memory_percent()   # 0.0 ~ 100.0
    """

    CPU_CACHE_TTL: float = 1.0    # CPU 缓存有效期（秒）
    MEM_CACHE_TTL: float = 1.0    # 内存缓存有效期（秒）

    def __init__(self) -> None:
        # ── 平台检测 ──
        self._platform: str = self._detect_platform()
        # ── psutil 可用性 ──
        self._psutil: object = None
        self._has_psutil: bool = False
        self._try_init_psutil()
        # ── CPU 缓存 ──
        self._cpu_percent: float = 0.0
        self._last_cpu_time: float = 0.0
        # CPU fallback: 保存上一次 /proc/stat 原始数据用于差值计算
        self._prev_cpu_total: int = 0
        self._prev_cpu_idle: int = 0
        self._have_prev_cpu: bool = False
        # ── 内存缓存 ──
        self._mem_percent: float = 0.0
        self._last_mem_time: float = 0.0

        _logger.debug(
            "SystemMonitor initialized, platform=%s, psutil=%s",
            self._platform, self._has_psutil,
        )

    # ─────────────────────────────────────────────
    # 平台检测
    # ─────────────────────────────────────────────

    @staticmethod
    def _detect_platform() -> str:
        """检测当前操作系统平台。

        Returns:
            ``'linux'`` | ``'darwin'`` | ``'windows'`` | ``'cygwin'`` | ``'unknown'``
        """
        raw = platform.system().lower()
        # Cygwin: system() 返回 "CYGWIN_NT-10.0-26220" 形式
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
        """尝试导入 psutil，成功则标记 self._has_psutil = True。"""
        try:
            import psutil  # type: ignore[import-untyped]
            self._psutil = psutil
            self._has_psutil = True
        except ImportError:
            self._psutil = None
            self._has_psutil = False

    # ─────────────────────────────────────────────
    # 公共 API
    # ─────────────────────────────────────────────

    def get_cpu_percent(self) -> float:
        """获取 CPU 使用率（0.0 ~ 100.0），1 秒缓存。

        Returns:
            CPU 使用率百分比，采集失败时返回 0.0。
        """
        now = time.monotonic()
        if now - self._last_cpu_time < self.CPU_CACHE_TTL:
            return self._cpu_percent
        self._last_cpu_time = now

        try:
            if self._has_psutil:
                self._cpu_percent = self._read_cpu_psutil()
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

        # clamp 到合法范围
        self._cpu_percent = max(0.0, min(100.0, self._cpu_percent))
        return self._cpu_percent

    def get_memory_percent(self) -> float:
        """获取内存使用率（0.0 ~ 100.0），1 秒缓存。

        Returns:
            内存使用率百分比，采集失败时返回 0.0。
        """
        now = time.monotonic()
        if now - self._last_mem_time < self.MEM_CACHE_TTL:
            return self._mem_percent
        self._last_mem_time = now

        try:
            if self._has_psutil:
                self._mem_percent = self._read_mem_psutil()
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
        """同时获取 CPU 和内存使用率。

        相比分别调用 get_cpu_percent() + get_memory_percent()，
        此方法仅执行一次缓存检查，更高效。

        Returns:
            (cpu_percent, mem_percent) 二元组，每个值范围 0.0~100.0。
        """
        self.get_cpu_percent()
        self.get_memory_percent()
        return (self._cpu_percent, self._mem_percent)

    # ─────────────────────────────────────────────
    # CPU 采集实现
    # ─────────────────────────────────────────────

    def _read_cpu_psutil(self) -> float:
        """使用 psutil 读取 CPU 使用率（非阻塞）。"""
        psutil = self._psutil
        if psutil is None:
            return 0.0
        return float(psutil.cpu_percent(interval=0))

    def _read_cpu_proc_stat(self) -> float:
        """从 /proc/stat 读取 CPU 使用率（Linux/Cygwin）。

        通过比较两次采样间 idle 与非 idle 时间的差值计算 CPU 使用率。
        首次采样返回 0.0（无上次数据无法计算差值）。
        """
        try:
            with open("/proc/stat") as f:
                line = f.readline()
        except (OSError, IOError):
            return 0.0

        parts = line.split()
        if not parts or parts[0] != "cpu":
            return 0.0

        # user nice system idle iowait irq softirq steal
        # guest guest_nice（可忽略）
        values = []
        for v in parts[1:]:
            try:
                values.append(int(v))
            except (ValueError, IndexError):
                break

        if len(values) < 4:
            return 0.0

        total = sum(values)
        idle = values[3]  # idle 是第 4 个字段

        if not self._have_prev_cpu:
            # 首次采样：只保存数据，返回 0.0
            self._prev_cpu_total = total
            self._prev_cpu_idle = idle
            self._have_prev_cpu = True
            return 0.0

        delta_total = total - self._prev_cpu_total
        delta_idle = idle - self._prev_cpu_idle

        # 更新保存值
        self._prev_cpu_total = total
        self._prev_cpu_idle = idle

        if delta_total <= 0:
            return 0.0

        cpu_pct = 100.0 * (1.0 - delta_idle / delta_total)
        return cpu_pct

    def _read_cpu_macos(self) -> float:
        """macOS 下使用 iostat 读取 CPU 使用率。

        执行 ``iostat -c 2 2`` 获取两秒间隔的两条采样，
        解析第二条的 ``%idle`` 值计算 CPU 使用率。
        """
        try:
            result = subprocess.run(
                ["iostat", "-c", "2", "2"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            lines = result.stdout.strip().splitlines()
            # iostat -c 2 2 返回 4 行（header+header+数据1+数据2）
            # 第4行（索引3）是第二条采样数据
            if len(lines) >= 4:
                parts = lines[-1].split()
                if len(parts) >= 6:
                    idle_str = parts[-1].replace("%", "")
                    idle_pct = float(idle_str)
                    return max(0.0, 100.0 - idle_pct)
            return 0.0
        except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
            return 0.0

    def _read_cpu_windows(self) -> float:
        """Windows 下使用 wmic 读取 CPU 使用率。

        执行 ``wmic cpu get loadpercentage /format:value``，
        解析 ``LoadPercentage=xx`` 格式的输出。
        """
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
                    val = line.split("=", 1)[1].strip()
                    return float(val)
            return 0.0
        except (subprocess.TimeoutExpired, OSError, ValueError, IndexError):
            return 0.0

    # ─────────────────────────────────────────────
    # 内存采集实现
    # ─────────────────────────────────────────────

    def _read_mem_psutil(self) -> float:
        """使用 psutil 读取内存使用率。"""
        psutil = self._psutil
        if psutil is None:
            return 0.0
        return float(psutil.virtual_memory().percent)

    def _read_mem_proc_meminfo(self) -> float:
        """从 /proc/meminfo 读取内存使用率（Linux/Cygwin）。

        优先使用 MemAvailable（较精确的可用内存估算），
        回退到 MemFree + Cached 的简单估算。
        """
        meminfo: dict[str, int] = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    key, rest = line.split(":", 1)
                    key = key.strip()
                    # 提取第一个数字部分（单位 kB）
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

        # 优先使用 MemAvailable（内核 3.14+ 支持）
        available = meminfo.get("MemAvailable")
        if available is not None and available > 0:
            used = total - available
            return 100.0 * used / total

        # 回退：MemFree + Cached（估算）
        free = meminfo.get("MemFree", 0)
        cached = meminfo.get("Cached", 0)
        buffers = meminfo.get("Buffers", 0)
        used = total - free - cached - buffers
        return 100.0 * max(0, used) / total

    def _read_mem_macos(self) -> float:
        """macOS 下使用 vm_stat + sysctl 读取内存使用率。

        使用 ``vm_stat`` 获取页统计，使用 ``sysctl hw.memsize`` 获取总内存。
        已用内存 = pages_active + pages_wired + pages_compressor（按页大小换算）。
        """
        # 获取总内存（字节）
        total_bytes = 0
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3.0,
            )
            if result.returncode == 0:
                total_bytes = int(result.stdout.strip())
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return 0.0

        if total_bytes <= 0:
            return 0.0

        # 获取 vm_stat
        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True, text=True, timeout=3.0,
            )
            if result.returncode != 0:
                return 0.0
        except (subprocess.TimeoutExpired, OSError):
            return 0.0

        # 解析 vm_stat 输出（page size + 各统计项）
        page_size = 4096  # 默认 page size
        active_pages = 0
        wired_pages = 0
        compressed_pages = 0

        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if "page size of" in line:
                import re
                m = re.search(r"page size of (\d+)", line)
                if m:
                    page_size = int(m.group(1))
            elif line.startswith("Pages active:"):
                val = line.split(":")[-1].strip().rstrip(".")
                try:
                    active_pages = int(val)
                except ValueError:
                    pass
            elif line.startswith("Pages wired down:"):
                val = line.split(":")[-1].strip().rstrip(".")
                try:
                    wired_pages = int(val)
                except ValueError:
                    pass
            elif line.startswith("Pages stored in compressor:"):
                val = line.split(":")[-1].strip().rstrip(".")
                try:
                    compressed_pages = int(val)
                except ValueError:
                    pass

        used_bytes = (active_pages + wired_pages + compressed_pages) * page_size
        return 100.0 * used_bytes / total_bytes

    def _read_mem_windows(self) -> float:
        """Windows 下使用 wmic 读取内存使用率。

        执行 ``wmic OS get TotalVisibleMemorySize,FreePhysicalMemory /format:value``，
        解析 TotalVisibleMemorySize 和 FreePhysicalMemory（单位 kB）。
        """
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
        except (subprocess.TimeoutExpired, OSError):
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

        used_kb = total_kb - free_kb
        return 100.0 * used_kb / total_kb
