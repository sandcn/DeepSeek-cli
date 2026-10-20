"""``_win_proc`` 在 Cygwin / MSYS2 下的能力回归测试。

Cygwin 的 ``os.name`` 是 ``posix``，但内核是 Windows：ctypes 没有
``WinDLL``，可用 ``CDLL("kernel32.dll")`` 调用 Toolhelp32 / Job /
TerminateProcess。修复前整个模块被 ``os.name == 'nt'`` 挡掉，导致
``bash_opt kill`` 无法杀死原生 Windows 子进程。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from src.tools import _win_proc

_CYGWIN = (sys.platform or "").startswith(("cygwin", "msys"))
_CYGWIN_ONLY = pytest.mark.skipif(not _CYGWIN, reason="需要 Cygwin / MSYS2")


@_CYGWIN_ONLY
def test_is_supported_and_winpid_mapping():
    assert _win_proc.is_supported() is True
    assert _win_proc.is_windows_like() is True
    assert _win_proc.is_cygwin_like() is True
    expected = int(open("/proc/self/winpid", encoding="utf-8").read().strip())
    assert _win_proc.to_winpid(os.getpid()) == expected
    assert _win_proc.process_create_time(expected) is not None
    assert _win_proc.is_process_alive(expected) is True


@_CYGWIN_ONLY
def test_job_processes_and_terminate():
    """Job 能收集成员 PID，TerminateJobObject 能杀掉成员。"""
    proc = subprocess.Popen(["/usr/bin/sleep", "60"])
    winpid = _win_proc.to_winpid(proc.pid)
    assert winpid
    job = _win_proc.create_job()
    assert job
    try:
        assert _win_proc.assign_process_to_job(job, winpid) is True
        deadline = time.monotonic() + 3.0
        members: list[int] = []
        while time.monotonic() < deadline:
            members = _win_proc.job_processes(job)
            if winpid in members:
                break
            time.sleep(0.05)
        assert winpid in members, members
        assert _win_proc.terminate_job(job) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _win_proc.is_process_alive(winpid):
            time.sleep(0.05)
        assert _win_proc.is_process_alive(winpid) is False
    finally:
        _win_proc.close_handle(job)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)


@_CYGWIN_ONLY
def test_find_processes_by_name_matches_spawned_process():
    """按 exe 名查找能命中刚启动的进程（大小写不敏感）。"""
    proc = subprocess.Popen(["/usr/bin/sleep", "60"])
    try:
        winpid = _win_proc.to_winpid(proc.pid)
        deadline = time.monotonic() + 3.0
        found: list[int] = []
        while time.monotonic() < deadline:
            found = _win_proc.find_processes_by_name("SLEEP.EXE")
            if winpid in found:
                break
            time.sleep(0.05)
        assert winpid in found, (winpid, found)
    finally:
        proc.kill()
        proc.wait(timeout=3)
