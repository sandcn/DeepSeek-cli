"""后台任务的 Windows 进程 / Job 归属登记表。

为什么需要单独登记：

  - **Cygwin / MSYS2** 的进程号是 POSIX PID，而窗口枚举、Job 对象、
    ``TerminateProcess`` 使用的是 Windows PID。两者不是同一套编号；
  - 后台命令可能是 ``bash -c 'regedit.exe'`` 这种「Cygwin shell 直接 exec
    原生程序」的形态——执行后 ``/proc/<pid>/winpid`` 会指向新的 Windows
    进程，甚至 POSIX 进程本身退出（GUI / App Execution Alias）；
  - ``bash_opt`` 的 screenshot / windows / 输入 op 只拿得到任务记录里的
    POSIX pid，拿不到 Job 句柄。

本模块把「任务 POSIX pid → 启动时的 Windows pid / Job 句柄 / 可执行名提示 /
启动时间」登记在一张进程内表里，供 ``_screenshot.win.resolve_window_pids``
与 ``_bash_support.kill_process_tree`` 查询。表项在任务记录移除时注销，
避免 POSIX PID 被系统复用后命中旧任务。

线程安全：后台任务、采样协程与 GUI 工具可能在不同线程并发访问，全部操作
由 ``RLock`` 保护。查询函数只返回快照，不暴露内部容器。
"""

from __future__ import annotations

import threading
import time
from typing import Optional

_LOCK = threading.RLock()

#: POSIX pid（任务记录里的 ``pid``）→ 启动时观察到的 Windows pid
_WINPIDS: dict[int, int] = {}

#: POSIX pid → 任务 Job 句柄（Windows；无 Job 时不登记）
_JOBS: dict[int, int] = {}

#: POSIX pid → 可执行名提示（来自命令行首个 token，小写 basename）
_HINTS: dict[int, str] = {}

#: POSIX pid → 任务启动时间（Windows FILETIME 100ns，用于过滤「任务启动后
#: 新出现」的同名进程；无 Windows 时为 0）
_START_TIMES: dict[int, int] = {}


def register(posix_pid: int, winpid: int | None, *,
             job: int | None = None,
             hint: str | None = None,
             start_time: int | None = None) -> None:
    """登记一个任务（``posix_pid`` 为任务记录里的进程号）。

    Args:
        posix_pid: 任务进程号（Cygwin 下是 POSIX pid；原生 Windows 下即
            Windows pid）。
        winpid: 启动时观察到的 Windows pid（取不到时传 ``None``）。
        job: 任务 Job 句柄（无 Job 传 ``None``）。
        hint: 可执行名提示（小写 basename，如 ``notepad.exe``）。
        start_time: 任务启动时间（Windows FILETIME；无则 ``None``）。
    """
    try:
        key = int(posix_pid)
    except (TypeError, ValueError):
        return
    if key <= 0:
        return
    with _LOCK:
        if winpid:
            try:
                _WINPIDS[key] = int(winpid)
            except (TypeError, ValueError):
                pass
        if job:
            try:
                _JOBS[key] = int(job)
            except (TypeError, ValueError):
                pass
        if hint:
            _HINTS[key] = str(hint).strip().lower()
        if start_time:
            try:
                _START_TIMES[key] = int(start_time)
            except (TypeError, ValueError):
                pass


def winpid_for(posix_pid: int) -> Optional[int]:
    """返回登记过的 Windows pid（未登记返回 ``None``）。"""
    try:
        key = int(posix_pid)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        return _WINPIDS.get(key)


def job_for(posix_pid: int) -> Optional[int]:
    """返回登记过的 Job 句柄（未登记返回 ``None``）。"""
    try:
        key = int(posix_pid)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        return _JOBS.get(key)


def hint_for(posix_pid: int) -> Optional[str]:
    """返回任务的可执行名提示（未登记返回 ``None``）。"""
    try:
        key = int(posix_pid)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        return _HINTS.get(key)


def start_time_for(posix_pid: int) -> Optional[int]:
    """返回任务启动时间（Windows FILETIME；未登记返回 ``None``）。"""
    try:
        key = int(posix_pid)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        return _START_TIMES.get(key)


def job_processes(job: int | None) -> list[int]:
    """返回 Job 内当前存活的 Windows pid 列表（无 Job / 查询失败返回空表）。"""
    if not job:
        return []
    try:
        from . import _win_proc

        return _win_proc.job_processes(int(job))
    except Exception:  # pragma: no cover - 依赖系统调用
        return []


def unregister(posix_pid: int) -> None:
    """注销一个任务的全部登记信息（幂等）。"""
    try:
        key = int(posix_pid)
    except (TypeError, ValueError):
        return
    with _LOCK:
        _WINPIDS.pop(key, None)
        _JOBS.pop(key, None)
        _HINTS.pop(key, None)
        _START_TIMES.pop(key, None)


def clear() -> None:
    """清空登记表（测试 / 进程退出清理用）。"""
    with _LOCK:
        _WINPIDS.clear()
        _JOBS.clear()
        _HINTS.clear()
        _START_TIMES.clear()


def now_filetime() -> int:
    """当前时间对应的 Windows FILETIME（100ns，1601-01-01 起）。

    仅用于「进程创建时间是否晚于任务启动」的比较；非 Windows 平台也返回
    同一套数值，便于逻辑统一（``_win_proc`` 的创建时间只在 Windows 可读）。
    """
    return int((time.time() + 11644473600) * 10_000_000)


__all__ = [
    "clear",
    "hint_for",
    "job_for",
    "job_processes",
    "now_filetime",
    "register",
    "start_time_for",
    "unregister",
    "winpid_for",
]
