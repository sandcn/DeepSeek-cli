"""src/tui/_system_monitor — _SystemMonitor 跨平台 CPU/内存采集单元测试。

覆盖：
  - 平台检测（linux/darwin/windows/cygwin/unknown）
  - psutil 路径 / 无 psutil 回退路径
  - /proc/stat CPU 解析（含首次采样、delta 计算、异常兜底）
  - /proc/meminfo 内存解析（MemAvailable 与 MemFree+Cached+Buffers 双口径）
  - macOS iostat/sysctl/vm_stat 解析（标题行定位 idle、page size）
  - Windows wmic CPU/内存解析
  - 缓存 TTL、数值钳制、后台刷新启动（单飞）
所有子进程与文件 IO 均被 mock。
"""

from __future__ import annotations

import io

import pytest

import src.tui._system_monitor as sm


class _Clock:
    """可变假时钟：手动推进以控制缓存 TTL 行为。"""

    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


@pytest.fixture
def monitor(monkeypatch):
    """构造 _SystemMonitor 并固定平台/禁用 psutil。"""
    monkeypatch.setattr(sm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sm.sys, "platform", "linux")
    m = sm._SystemMonitor()
    m._has_psutil = False
    m._psutil = None
    # 禁用后台刷新线程（测试同步路径）
    m._bg_started = True
    return m


def _fake_open(monkeypatch, path_to_content: dict):
    """按路径注入假文件内容；未注入的 /proc/* 路径抛 OSError。"""
    import builtins

    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        s = str(path)
        if s in path_to_content:
            return io.StringIO(path_to_content[s])
        if s.startswith("/proc/"):
            raise OSError(f"no such file: {s}")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


# ── 平台检测 ─────────────────────────────────────────────

@pytest.mark.parametrize("system,expected", [
    ("linux", "linux"),
    ("android", "linux"),
    ("darwin", "darwin"),
    ("windows", "windows"),
    ("weirdos", "unknown"),
])
def test_detect_platform(monkeypatch, system, expected):
    monkeypatch.setattr(sm.platform, "system", lambda: system)
    monkeypatch.setattr(sm.sys, "platform", "linux")  # 屏蔽 cygwin 干扰
    assert sm._SystemMonitor._detect_platform() == expected


def test_detect_platform_cygwin(monkeypatch):
    monkeypatch.setattr(sm.platform, "system", lambda: "CYGWIN_NT-10.0")
    assert sm._SystemMonitor._detect_platform() == "cygwin"


def test_detect_platform_cygwin_sys_platform(monkeypatch):
    monkeypatch.setattr(sm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sm.sys, "platform", "cygwin")
    assert sm._SystemMonitor._detect_platform() == "cygwin"


# ── psutil 路径 ──────────────────────────────────────────

def test_psutil_path(monkeypatch):
    class _FakePsutil:
        @staticmethod
        def cpu_percent(interval=0):
            return 33.3

        @staticmethod
        def virtual_memory():
            return type("M", (), {"percent": 66.6})()

    monkeypatch.setattr(sm, "psutil", _FakePsutil, raising=False)
    m = sm._SystemMonitor()
    m._has_psutil = True
    m._psutil = _FakePsutil
    m._bg_started = True
    assert m.get_cpu_percent() == pytest.approx(33.3)
    assert m.get_memory_percent() == pytest.approx(66.6)


def test_no_psutil_import(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "psutil":
            raise ImportError("no psutil")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    m = sm._SystemMonitor()
    assert m._has_psutil is False
    assert m._psutil is None


# ── /proc/stat CPU ───────────────────────────────────────

def test_cpu_proc_stat_first_sample(monkeypatch, monitor):
    _fake_open(monkeypatch, {"/proc/stat": "cpu  100 0 100 200 0 0 0 0 0 0\n"})
    assert monitor.get_cpu_percent() == 0.0  # 首次采样无 delta
    assert monitor._have_prev_cpu is True


def test_cpu_proc_stat_delta(monkeypatch, monitor):
    monitor._have_prev_cpu = True
    monitor._prev_cpu_total = 400
    monitor._prev_cpu_idle = 200
    _fake_open(monkeypatch, {"/proc/stat": "cpu  500 0 100 300 0 0 0 0 0 0\n"})
    # total delta=500, idle delta=100 → 使用率 = 100*(1-100/500) = 80
    assert monitor.get_cpu_percent() == pytest.approx(80.0)


def test_cpu_proc_stat_read_error(monkeypatch, monitor):
    _fake_open(monkeypatch, {})  # /proc/stat 不可读 → OSError
    monkeypatch.setattr(sm.time, "monotonic", lambda: 999.0)  # 越过缓存
    assert monitor.get_cpu_percent() == 0.0


def test_cpu_proc_stat_bad_first_token(monkeypatch, monitor):
    _fake_open(monkeypatch, {"/proc/stat": "intr 1 2 3 4\n"})
    assert monitor.get_cpu_percent() == 0.0


def test_cpu_proc_stat_few_values(monkeypatch, monitor):
    _fake_open(monkeypatch, {"/proc/stat": "cpu 10 20\n"})
    assert monitor.get_cpu_percent() == 0.0


def test_cpu_clamped_to_100(monkeypatch, monitor):
    monitor._have_prev_cpu = True
    monitor._prev_cpu_total = 0
    monitor._prev_cpu_idle = 0
    _fake_open(monkeypatch, {"/proc/stat": "cpu  10 0 0 0 0 0 0 0 0 0\n"})
    # idle delta=0, total delta=10 → 100%
    assert monitor.get_cpu_percent() == 100.0


# ── /proc/meminfo 内存 ───────────────────────────────────

def test_mem_proc_meminfo_available(monkeypatch, monitor):
    _fake_open(monkeypatch, {
        "/proc/meminfo": (
            "MemTotal:       16000000 kB\n"
            "MemFree:         2000000 kB\n"
            "MemAvailable:    6000000 kB\n"
            "Buffers:          100000 kB\n"
            "Cached:           900000 kB\n"
        ),
    })
    # used = 16M - 6M = 10M → 62.5%
    assert monitor.get_memory_percent() == pytest.approx(62.5)


def test_mem_proc_meminfo_fallback_free_cached(monkeypatch, monitor):
    _fake_open(monkeypatch, {
        "/proc/meminfo": (
            "MemTotal:       10000000 kB\n"
            "MemFree:         2000000 kB\n"
            "Cached:          1000000 kB\n"
            "Buffers:          500000 kB\n"
        ),
    })
    # used = 10M - 2M - 1M - 0.5M = 6.5M → 65%
    assert monitor.get_memory_percent() == pytest.approx(65.0)


def test_mem_proc_meminfo_error(monkeypatch, monitor):
    _fake_open(monkeypatch, {})  # /proc/meminfo 不可读
    monkeypatch.setattr(sm.time, "monotonic", lambda: 999.0)
    assert monitor.get_memory_percent() == 0.0


# ── macOS 路径 ───────────────────────────────────────────

def _fake_subprocess(monkeypatch, results: dict):
    """按命令元组返回假 CompletedProcess。"""

    class _Result:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def fake_run(cmd, *args, **kwargs):
        key = tuple(cmd)
        assert key in results, f"未 mock 的命令: {cmd}"
        rc, out = results[key]
        return _Result(rc, out)

    monkeypatch.setattr(sm.subprocess, "run", fake_run)


def _darwin_monitor(monkeypatch):
    monkeypatch.setattr(sm.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sm.sys, "platform", "darwin")
    m = sm._SystemMonitor()
    m._has_psutil = False
    m._bg_started = True
    return m


def test_cpu_macos_parses_idle_column(monkeypatch):
    m = _darwin_monitor(monkeypatch)
    _fake_subprocess(monkeypatch, {
        ("iostat", "-c", "2", "2"): (0, "          disk0       cpu     load average\n"
                                       "KB/t tps  MB/s us sy id   1m   5m   15m\n"
                                       "12.3 4  0.1  12  8 80 0.5 0.4 0.3\n"),
    })
    assert m.get_cpu_percent() == pytest.approx(20.0)  # 100 - 80 idle


def test_cpu_macos_subprocess_error(monkeypatch):
    m = _darwin_monitor(monkeypatch)
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no iostat")))
    assert m.get_cpu_percent() == 0.0


def test_cpu_macos_timeout(monkeypatch):
    m = _darwin_monitor(monkeypatch)

    def timeout(*a, **k):
        raise TimeoutError("slow")

    monkeypatch.setattr(sm.subprocess, "run", timeout)
    assert m.get_cpu_percent() == 0.0


def test_mem_macos_full_path(monkeypatch):
    m = _darwin_monitor(monkeypatch)
    _fake_subprocess(monkeypatch, {
        ("sysctl", "-n", "hw.memsize"): (0, "8589934592\n"),  # 8 GiB
        ("vm_stat",): (0, "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                          "Pages free:                              123456.\n"
                          "Pages active:                          100000.\n"),
    })
    # free = 123456 * 16384 ≈ 2.023 GB → used ≈ 6.055 GB → ≈ 76.4%
    pct = m.get_memory_percent()
    assert 75.0 < pct < 78.0


def test_mem_macos_no_total(monkeypatch):
    m = _darwin_monitor(monkeypatch)
    _fake_subprocess(monkeypatch, {
        ("sysctl", "-n", "hw.memsize"): (1, ""),  # 失败
    })
    assert m.get_memory_percent() == 0.0


# ── Windows 路径 ─────────────────────────────────────────

def _windows_monitor(monkeypatch):
    monkeypatch.setattr(sm.platform, "system", lambda: "Windows")
    monkeypatch.setattr(sm.sys, "platform", "win32")
    m = sm._SystemMonitor()
    m._bg_started = True
    return m


def test_cpu_windows_parses_powershell(monkeypatch):
    m = _windows_monitor(monkeypatch)
    _fake_subprocess(monkeypatch, {
        ("powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Processor).LoadPercentage"): (0, "47\n"),
    })
    assert m.get_cpu_percent() == pytest.approx(47.0)


def test_mem_windows_parses_powershell(monkeypatch):
    m = _windows_monitor(monkeypatch)
    _fake_subprocess(monkeypatch, {
        ("powershell", "-NoProfile", "-Command",
         "$os = Get-CimInstance Win32_OperatingSystem; if ($os.TotalVisibleMemorySize) "
         "{ [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / "
         "$os.TotalVisibleMemorySize * 100, 2) } else { 0 }"): (0, "75\n"),
    })
    # used = (16M-4M)/16M = 75%
    assert m.get_memory_percent() == pytest.approx(75.0)


def test_windows_powershell_error(monkeypatch):
    m = _windows_monitor(monkeypatch)
    monkeypatch.setattr(sm.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no powershell")))
    assert m.get_cpu_percent() == 0.0
    assert m.get_memory_percent() == 0.0


# ── 缓存 / 钳制 / 后台刷新 ───────────────────────────────

def test_cpu_cache_ttl(monkeypatch, monitor):
    clock = _Clock(5.0)  # 初始时间远离 _last_cpu_time=0，首调用不被缓存拦截
    monkeypatch.setattr(sm.time, "monotonic", clock)
    calls = {"n": 0}

    def fake_read():
        calls["n"] += 1
        return 10.0

    monitor._read_cpu_proc_stat = fake_read  # type: ignore[method-assign]
    monitor.get_cpu_percent()      # 首次采集
    monitor.get_cpu_percent()      # 缓存命中
    assert calls["n"] == 1
    clock.advance(2.0)             # 越过 TTL
    monitor.get_cpu_percent()      # 重新采集
    assert calls["n"] == 2


def test_cpu_clamped_range(monkeypatch, monitor):
    clock = _Clock(5.0)  # 初始时间远离 _last_cpu_time=0
    monkeypatch.setattr(sm.time, "monotonic", clock)
    monitor._read_cpu_proc_stat = lambda: 150.0  # type: ignore[method-assign]
    assert monitor.get_cpu_percent() == 100.0
    clock.advance(2.0)
    monitor._read_cpu_proc_stat = lambda: -10.0  # type: ignore[method-assign]
    assert monitor.get_cpu_percent() == 0.0


def test_get_cpu_and_mem_sync_path(monkeypatch, monitor):
    monitor._bg_started = False  # 走同步采集分支
    monitor._read_cpu_proc_stat = lambda: 20.0  # type: ignore[method-assign]
    monitor._read_mem_proc_meminfo = lambda: 30.0  # type: ignore[method-assign]
    cpu, mem = monitor.get_cpu_and_mem()
    assert cpu == 20.0
    assert mem == 30.0


def test_bg_refresh_starts_once(monkeypatch):
    monkeypatch.setattr(sm.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(sm.sys, "platform", "darwin")
    m = sm._SystemMonitor()
    m._has_psutil = False

    starts = []

    class _FakeThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            starts.append(1)

    monkeypatch.setattr(sm.threading, "Thread", _FakeThread)
    m.get_cpu_and_mem()
    m.get_cpu_and_mem()
    assert len(starts) == 1  # 单飞：仅启动一次


def test_bg_loop_calls_collectors_and_sleeps(monkeypatch):
    m = sm._SystemMonitor()
    calls = {"cpu": 0, "mem": 0}

    def cpu():
        calls["cpu"] += 1

    def mem():
        calls["mem"] += 1

    m.get_cpu_percent = cpu  # type: ignore[method-assign]
    m.get_memory_percent = mem  # type: ignore[method-assign]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) >= 2:
            raise KeyboardInterrupt  # 结束无限循环

    monkeypatch.setattr(sm.time, "sleep", sleep)
    with pytest.raises(KeyboardInterrupt):
        m._bg_loop()
    assert calls["cpu"] == 2
    assert calls["mem"] == 2
    assert sleeps == [2.0, 2.0]
