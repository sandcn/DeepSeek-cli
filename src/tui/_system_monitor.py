"""系统监控模块 — _SystemMonitor 跨平台 CPU/内存采集（移植自旧 _bottom_bar/_monitor.py）。

惰性初始化：首次采集才检测平台/尝试导入 psutil；1 秒缓存消峰。
供 InkSession 每 2 秒刷新 model.status.cpu/mem（输入区顶部分隔线显示）。
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import sys
import threading
import time
from typing import Any

_logger = logging.getLogger(__name__)


class _SystemMonitor:
    """跨平台系统监控 — CPU 与内存使用率采集。"""

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
        # 子进程平台后台刷新（darwin/windows 无 psutil 时启用）——渲染线程
        # 不阻塞等待 iostat/sysctl/vm_stat/wmic 子进程（方向3 性能）。
        self._bg_started: bool = False
        # ★ P2-1（review 方向）：后台线程启动锁——检查/置位原子化，防并发
        #   首次调用（get_cpu_and_mem 双线程同时见 _bg_started=False）启动
        #   双后台线程。
        self._bg_lock = threading.Lock()
        self._bg_interval: float = 2.0

    @staticmethod
    def _detect_platform() -> str:
        raw = platform.system().lower()
        if "cygwin" in raw or (sys.platform and "cygwin" in sys.platform):
            return "cygwin"
        if raw == "linux" or raw == "android":
            # Android（Termux）为 Linux 类：/proc/meminfo 可读，/proc/stat 可能被拒
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
            _logger.warning("读取 CPU 使用率失败", exc_info=True)
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
            _logger.warning("读取内存使用率失败", exc_info=True)
            self._mem_percent = 0.0
        self._mem_percent = max(0.0, min(100.0, self._mem_percent))
        return self._mem_percent

    def get_cpu_and_mem(self) -> tuple[float, float]:
        # 子进程平台（darwin/windows 且无 psutil）：同步采集可能阻塞渲染线程
        # 数秒（iostat 含 2s 采样 / sysctl+vm_stat 双子进程最坏 ~11s）——启动
        # 后台 daemon 线程持续刷新缓存，渲染线程只读缓存（方向3 性能）。
        if (
            not self._bg_started
            and not self._has_psutil
            and self._platform in ("darwin", "windows")
        ):
            self._start_bg_refresh()
        elif not self._bg_started:
            self.get_cpu_percent()
            self.get_memory_percent()
        return (self._cpu_percent, self._mem_percent)

    def _start_bg_refresh(self) -> None:
        """启动后台刷新线程（幂等；daemon 线程随进程退出自动终止）。

        ★ P2-1（review 方向）：检查/置位在 ``_bg_lock`` 内原子完成——修复前
        无锁（``self._bg_started = True`` 在启动前裸置位），并发首次调用
        （get_cpu_and_mem 双线程同时通过 ``not self._bg_started`` 检查）可
        启动双后台线程。锁内二次检查保证仅启动一个。
        """
        with self._bg_lock:
            if self._bg_started:
                return
            self._bg_started = True
            try:
                t = threading.Thread(target=self._bg_loop, daemon=True,
                                     name="tui-sysmon")
                t.start()
            except Exception:
                _logger.debug("系统监控后台线程启动失败", exc_info=True)
                self._bg_started = False

    def _bg_loop(self) -> None:
        """后台刷新循环：每 2 秒采集一次 CPU/MEM（get_* 内部含 1s TTL 缓存）。"""
        while True:
            try:
                self.get_cpu_percent()
                self.get_memory_percent()
            except Exception:
                _logger.debug("系统监控后台刷新异常", exc_info=True)
            time.sleep(self._bg_interval)

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
            # ★ P1-2（review 方向）：按标题行动态定位 idle 列——macOS iostat
            #   数据行格式 ``us sy id 1m 5m 15m``（us=0, sy=1, id=2）；修复前
            #   取 ``parts[-1]`` 为 15m load average → CPU 使用率恒 ≈98% 错误。
            #   标题行定位比硬编码索引稳妥（iostat 输出可能含磁盘列，如
            #   ``KB/t tps MB/s us sy id ...``）。
            idle_idx: int | None = None
            for line in lines:
                toks = line.split()
                if "us" in toks and "sy" in toks and "id" in toks:
                    idle_idx = toks.index("id")
                    break
            if idle_idx is None:
                return 0.0
            # 数据行从后向前取（``iostat -c 2 2`` 最后一行是最新采样）
            for line in reversed(lines):
                toks = line.split()
                if len(toks) > idle_idx:
                    idle_str = toks[idle_idx].replace("%", "")
                    idle = float(idle_str)
                    return max(0.0, 100.0 - idle)
            return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: iostat -c 2 2", exc_info=True)
            return 0.0
        except (OSError, ValueError, IndexError):
            return 0.0

    def _read_cpu_windows(self) -> float:
        try:
            # ★ P2（review 2026-08-22）：wmic 已被 Windows 11 24H2 弃用——
            #   改用 PowerShell Get-CimInstance 读 CPU LoadPercentage。
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor).LoadPercentage"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            text = result.stdout.strip()
            if not text:
                return 0.0
            return float(text.splitlines()[-1].strip())
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: powershell cpu load", exc_info=True)
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
        free_pages = 0
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if "page size of" in line:
                m = re.search(r"page size of (\d+)", line)
                if m:
                    page_size = int(m.group(1))
            elif line.startswith("Pages free:"):
                try:
                    free_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
        # ★ P2-6（review 方向）：改用 ``total - free`` 完整口径——修复前
        #   ``active + wired + stored_in_compressor`` 系统性低估真实内存使用
        #   （漏掉 inactive/speculative/purgeable 等已分配页面；且 macOS
        #   vm_stat 实际关键字为 "Pages occupied by compressor"，旧关键字
        #   "Pages stored in compressor" 解析恒 0）。total - free 近似"已
        #   分配内存"，口径完整且与 Linux ``MemTotal - MemFree`` 近似一致。
        used_bytes = max(0, total_bytes - free_pages * page_size)
        return 100.0 * used_bytes / total_bytes

    def _read_mem_windows(self) -> float:
        try:
            # ★ P2（review 2026-08-22）：wmic 已被 Windows 11 24H2 弃用——
            #   改用 PowerShell Get-CimInstance 计算内存占用百分比。
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "$os = Get-CimInstance Win32_OperatingSystem; "
                 "if ($os.TotalVisibleMemorySize) { "
                 "[math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / "
                 "$os.TotalVisibleMemorySize * 100, 2) } else { 0 }"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            text = result.stdout.strip()
            if not text:
                return 0.0
            return float(text.splitlines()[-1].strip())
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: powershell mem", exc_info=True)
            return 0.0
        except (OSError, ValueError, IndexError):
            return 0.0


__all__ = ["_SystemMonitor"]
