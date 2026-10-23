"""系统监控平台分支解析（macOS / Windows）回归测试（2026-10-23）。

说明：当前运行环境为 Cygwin，无法在 macOS/Windows 实机执行；本测试用
``subprocess.run`` 的模拟输出覆盖解析分支，验证命令输出 → 百分比的换算。
"""

from __future__ import annotations

import subprocess

import pytest

from src.tui._system_monitor import _SystemMonitor


class _Completed(subprocess.CompletedProcess):
    def __init__(self, stdout: str, returncode: int = 0):
        super().__init__(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_windows_cpu_percent(monkeypatch) -> None:
    monitor = _SystemMonitor()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Completed("42\n"),
    )
    assert monitor._read_cpu_windows() == pytest.approx(42.0)


def test_windows_memory_percent(monkeypatch) -> None:
    monitor = _SystemMonitor()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Completed("65.5\n"),
    )
    assert monitor._read_mem_windows() == pytest.approx(65.5)


def test_macos_cpu_percent(monkeypatch) -> None:
    monitor = _SystemMonitor()
    output = (
        "          disk0       cpu\n"
        "  KB/t tps MB/s us sy id 1m 5m 15m\n"
        "  0.00   0  0.0 10 20 70 1.0 2.0 3.0\n"
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Completed(output),
    )
    assert monitor._read_cpu_macos() == pytest.approx(30.0)


def test_macos_memory_percent(monkeypatch) -> None:
    monitor = _SystemMonitor()
    total = 4096 * 1000  # 1000 页
    free = 250
    responses = [
        _Completed(f"{total}\n"),  # sysctl -n hw.memsize
        _Completed(
            "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
            f"Pages free: {free}.\n"
        ),  # vm_stat
    ]

    def _run(*_a, **_k):
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", _run)
    # used = total - free*4096 = 75% 
    assert monitor._read_mem_macos() == pytest.approx(75.0)


def test_windows_bad_output_returns_zero(monkeypatch) -> None:
    monitor = _SystemMonitor()
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _Completed("N/A\n", returncode=1),
    )
    assert monitor._read_cpu_windows() == 0.0
    assert monitor._read_mem_windows() == 0.0
