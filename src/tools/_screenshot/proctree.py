"""进程树收集（跨平台）——截图后端据此定位目标进程及其子进程的窗口。

幂等、无副作用：仅读取系统进程表（/proc、Toolhelp32、ps），不做任何修改。

★ Cygwin / MSYS2 特例（性能红线）：该平台的 ``/proc`` 由运行时按需模拟，
``os.listdir`` 很快，但逐个读取 ``/proc/<pid>/status`` 需数百毫秒（实测单
进程约 0.2s、整表 3~4s）。窗口定位（``resolve_window_pids``）在**每次**
截图 / 输入注入时都会被调用，且注入时还会因前台确认被调用多次——沿用
``/proc/<pid>/status`` 会让「操作一次 GUI」平白多花约 10 秒。这类平台因此
改走 ``ps`` 取 POSIX 父子关系（单次约 0.12s，见 :func:`_cygwin_children_map`）。

注意：**不能**用「``/proc/<pid>/winpid`` + Windows 进程表（Toolhelp32）」合成
父子关系——Cygwin 的 ``fork`` / ``spawn`` 会让新进程的 Windows 父进程指向
内部临时进程而非 POSIX 父进程（实测：直接子进程的 Windows 父 PID 既不是父
进程的 winpid、也不在进程表内），据此合成会漏掉真实的子进程。``winpid``
只用于把 POSIX PID 映射成 Windows PID（:func:`read_winpid`，很快）。
"""

from __future__ import annotations

import logging
import os
import subprocess

from .._bash_support import _children_map_from_ps
from . import winapi

logger = logging.getLogger(__name__)

#: 树深度上限（防御进程表异常造成的环路/超深树）
DEFAULT_MAX_DEPTH = 32

#: ps 回退命令超时（秒）
_PS_TIMEOUT = 10.0


def collect_process_tree(root_pid: int, *, max_depth: int = DEFAULT_MAX_DEPTH) -> list[int]:
    """返回 ``root_pid`` 及其全部后代 PID（广度去重、含根）。

    Args:
        root_pid: 根进程 PID（非法值返回空表）。
        max_depth: 最大下钻深度。

    Returns:
        ``[root_pid, *descendants]``；无法读取系统进程表时仅返回 ``[root_pid]``。
    """
    if not isinstance(root_pid, int) or isinstance(root_pid, bool) or root_pid <= 0:
        return []
    children = _children_map()
    if children is None:
        return [root_pid]
    result = [root_pid]
    seen = {root_pid}
    stack = [(root_pid, 0)]
    while stack:
        pid, depth = stack.pop()
        if depth >= max_depth:
            continue
        for child in children.get(pid, ()):
            if child in seen:
                continue
            seen.add(child)
            result.append(child)
            stack.append((child, depth + 1))
    return result


def _children_map() -> dict[int, list[int]] | None:
    """构建 ``父 PID → [子 PID]`` 映射（无法获取返回 None）。

    平台策略（按优先级）：

      1. **Cygwin / MSYS2**：``ps``（:func:`_cygwin_children_map`）——该平台的
         ``/proc/<pid>/status`` 是慢速模拟层（单进程约 0.2s），绝不能走；
      2. ``/proc/<pid>/status`` 的 ``PPid`` 扫描（Linux / Android Termux，
         单次遍历 O(N) 且最快）；
      3. **Windows 原生**：Toolhelp32 进程表；
      4. 其它（macOS / *BSD）：``ps``。
    """
    if winapi.is_cygwin_like():
        mapping = _cygwin_children_map()
        if mapping is not None:
            return mapping
    if os.path.isdir("/proc") and os.path.exists("/proc/self/status"):
        mapping = _proc_children_map()
        if mapping is not None:
            return mapping
    if winapi.is_windows_platform():
        return _windows_children_map()
    return _ps_children_map()


def _cygwin_children_map() -> dict[int, list[int]] | None:
    """Cygwin / MSYS2：用 ``ps`` 取 ``父 PID → [子 PID]``（POSIX 语义）。

    为什么不用 ``/proc/<pid>/status``：Cygwin 的 ``/proc`` 由运行时按需模拟，
    逐个读取 ``status`` 需数百毫秒（实测单进程约 0.2s、整表 3~4s），而
    ``ps`` 一次只要约 0.12s。窗口定位位于截图 / 输入注入的每次调用路径上，
    这里的差距就是「操作 GUI 卡约 10 秒」与「瞬时」的差距。

    为什么不用 ``winpid`` + Windows 进程表合成：Cygwin 的 ``fork`` / ``spawn``
    会让新进程的 Windows 父进程指向内部临时进程（实测直接子进程的 Windows
    父 PID 既不是父进程的 winpid、也不在进程表内），据此合成会**漏掉真实
    子进程**；因此 ``winpid`` 只用于 POSIX PID → Windows PID 的单点映射。

    ``ps`` 的命令形态由 ``_bash_support._children_map_from_ps`` 统一提供
    （``ps -eo pid=,ppid=`` → ``ps -ef`` → ``ps ax``）：Cygwin 的 procps 只
    支持后两种，这段平台差异已经在那里验证过，这里直接复用，避免两处漂移。

    Returns:
        ``父 PID → [子 PID]``（POSIX PID）；``ps`` 不可用（输出为空）时返回
        ``None``（调用方回退其它通道，保正确性）。
    """
    return _children_map_from_ps() or None


def _proc_children_map() -> dict[int, list[int]] | None:
    """Linux / Android Termux 的 ``/proc/<pid>/status``（PPid 字段）扫描。

    ★ 慢速模拟 ``/proc`` 的平台（Cygwin / MSYS2）不要走到这里：那些平台的
    ``_children_map`` 已由 :func:`_cygwin_children_map` 接管（见模块文档）。
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    mapping: dict[int, list[int]] = {}
    for entry in entries:
        if not entry.isdigit():
            continue
        ppid = _read_proc_ppid(entry)
        if ppid is None:
            continue
        mapping.setdefault(ppid, []).append(int(entry))
    return mapping


def _read_proc_ppid(pid_str: str) -> int | None:
    """读取 ``/proc/<pid>/status`` 的 PPid（失败返回 None）。"""
    try:
        with open(f"/proc/{pid_str}/status", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("PPid:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _windows_children_map() -> dict[int, list[int]] | None:
    """Windows 原生进程表（Toolhelp32 快照）。"""
    try:
        processes = winapi.list_processes()
    except OSError:
        return None
    mapping: dict[int, list[int]] = {}
    for pid, ppid, _name in processes:
        mapping.setdefault(ppid, []).append(pid)
    return mapping


def _ps_children_map() -> dict[int, list[int]] | None:
    """BSD/macOS 回退：解析 ``ps -A -o pid=,ppid=``。"""
    try:
        completed = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="],
            capture_output=True, text=True, timeout=_PS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ps 进程表读取失败: %s", exc)
        return None
    mapping: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        mapping.setdefault(ppid, []).append(pid)
    return mapping


def read_winpid(pid: int) -> int | None:
    """Cygwin/MSYS2：读取 ``/proc/<pid>/winpid``（POSIX pid → Windows pid）。

    非 Cygwin 或进程不存在返回 None。
    """
    try:
        with open(f"/proc/{pid}/winpid", encoding="utf-8", errors="replace") as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "collect_process_tree",
    "read_winpid",
]
