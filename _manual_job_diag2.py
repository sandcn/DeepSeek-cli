"""诊断 2：Job 继承（cmd → ping）与父 Job 限制标志。"""

from __future__ import annotations

import ctypes
import subprocess
import time

from src.tools import _win_proc
from src.tools._bash_support import (
    create_process_job,
    job_active_processes,
    terminate_process_job,
)


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def parent_job_limit_flags() -> None:
    """查询当前进程所在 Job 的限制标志。"""
    lib = _win_proc._load_kernel32()
    job_of_current = ctypes.c_void_p()
    # IsProcessInJob(GetCurrentProcess(), NULL, &result)
    result = ctypes.c_int()
    lib.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(result))
    print("in_job:", result.value)
    # 枚举当前进程的 Job：用 QueryInformationJobObject 需要句柄；
    # 简化：直接看 CreateJobObject 后 assign 的返回与继承行为即可。
    # 这里改用 JobObjectExtendedLimitInformation(9) 查询父 Job：
    # 需要句柄，暂不实现，输出占位。
    print("parent job limit flags: (需句柄，见继承实测)")


def cmd_inherit() -> None:
    print("\n--- cmd → ping 继承 ---")
    proc = subprocess.Popen(
        ["cmd", "/c", "ping -n 60 127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    job = create_process_job(proc.pid)
    print("job:", job)
    for i in range(10):
        time.sleep(0.2)
        print(" t=%.1fs active=%s" % (0.2 * (i + 1), job_active_processes(job)))
    print("terminate:", terminate_process_job(job))
    time.sleep(0.3)
    print("active_after:", job_active_processes(job))
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def powershell_inherit_late() -> None:
    """PowerShell 先启动、稍后（1s）再启动子进程：验证 assign 之后的继承。"""
    print("\n--- PowerShell：assign 后 1s 才启动子进程 ---")
    proc = subprocess.Popen(
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
         "-NoLogo", "-NoProfile", "-Command",
         "Start-Sleep -Milliseconds 1000; ping -n 60 127.0.0.1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    job = create_process_job(proc.pid)
    print("job:", job)
    for i in range(12):
        time.sleep(0.2)
        print(" t=%.1fs active=%s" % (0.2 * (i + 1), job_active_processes(job)))
    print("terminate:", terminate_process_job(job))
    time.sleep(0.3)
    print("active_after:", job_active_processes(job))
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


if __name__ == "__main__":
    parent_job_limit_flags()
    cmd_inherit()
    powershell_inherit_late()
