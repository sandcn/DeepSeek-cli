"""进程树收集（跨平台）——截图后端据此定位目标进程及其子进程的窗口。

幂等、无副作用：仅读取系统进程表（/proc、Toolhelp32、ps），不做任何修改。
"""

from __future__ import annotations

import logging
import os
import subprocess

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
    """构建 ``父 PID → [子 PID]`` 映射（无法获取返回 None）。"""
    if os.path.isdir("/proc") and os.path.exists("/proc/self/status"):
        mapping = _proc_children_map()
        if mapping is not None:
            return mapping
    if winapi.is_windows_platform():
        return _windows_children_map()
    return _ps_children_map()


def _proc_children_map() -> dict[int, list[int]] | None:
    """Linux / Cygwin 的 ``/proc/<pid>/status``（PPid 字段）扫描。"""
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
