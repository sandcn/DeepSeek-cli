"""Windows 原生进程表 / 终止 / 存活判定（ctypes 绑定）。

bash 后台任务（``bash_opt`` 的 ``op=kill``、ESC 中断兜底）必须杀掉**整棵
进程树**。Windows 没有 ``/proc``，也不一定有 ``ps``——修复前
``_bash_support._children_map`` 在原生 Windows 上只能退回 ``ps``（通常不
存在）→ 后代收集恒为空，杀树完全依赖 ``taskkill /F /T`` 的隐式遍历。一旦
根进程先退出（后台 shell 结束、子进程成为常驻孤儿），``taskkill /PID 根``
找不到进程，后代全部残留——即「bash_opt kill 不能杀死整个进程树」。

Toolhelp32 快照的 ``th32ParentProcessID`` 记录的是**创建时**的父 PID，父进程
退出后不会改变，因此仍能定位这些孤儿后代（:func:`list_processes`）。

本模块提供四类能力，供 ``_bash_support`` 在原生 Windows 上使用：
  - :func:`list_processes`：Toolhelp32 进程表（``(pid, ppid, exe 名)``）；
  - :func:`process_create_time`：进程创建时间（pid 复用防护）；
  - :func:`terminate_process`：``TerminateProcess``（比 taskkill 快，且
    不受命令行解析 / 进程名转义影响）；
  - :func:`is_process_alive`：``OpenProcess`` + ``GetExitCodeProcess``
    （权限不足视为「存在」，不把杀不掉的高权限进程谎报成已退出）。

平台边界：服务**原生 Windows 与 Cygwin / MSYS2**。Cygwin / MSYS2 的 pid
是 POSIX pid，涉及 Windows 对象（Job / TerminateProcess / Toolhelp32）时
需先经 ``/proc/<pid>/winpid`` 映射（:func:`to_winpid`）；POSIX 进程树仍由
``_bash_support`` 用 ``ps`` 维护。Cygwin 的 ctypes 没有 ``WinDLL``，但
x86_64 只有一种调用约定，用 ``ctypes.CDLL`` 可直接调用 kernel32。

所有函数在非 Windows / API 不可用 / 调用失败时返回空值，绝不抛异常——
杀进程树是中断热路径，辅助功能失败不应把调用方一起拖崩。
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

# ── Win32 常量 ──────────────────────────────────────────
TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260
#: ``OpenProcess`` 访问权限：终止进程
PROCESS_TERMINATE = 0x00000001
#: ``OpenProcess`` 访问权限：修改作业配额（AssignProcessToJobObject 需要）
PROCESS_SET_QUOTA = 0x0100
#: ``OpenProcess`` 访问权限：查询退出码 / 进程时间（Vista+，低权限即可）
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: ``GetExitCodeProcess`` 的「仍在运行」标志
STILL_ACTIVE = 259
#: ``OpenProcess`` 失败：拒绝访问（进程存在但权限不足）
ERROR_ACCESS_DENIED = 5
#: ``QueryInformationJobObject`` 的信息类：基本统计（含活动进程数）
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
#: ``QueryInformationJobObject`` 的信息类：Job 内进程 PID 列表
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
#: ``QueryInformationJobObject`` PID 列表的预分配容量（Cygwin 下项目任务
#: 的后代数远小于此；超出时系统返回 ERROR_MORE_DATA，调用方按空表处理）
_JOB_PID_LIST_CAPACITY = 4096
#: ``CreateToolhelp32Snapshot`` 失败返回值
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def is_cygwin_like() -> bool:
    """当前解释器是否为 Cygwin / MSYS2（POSIX pid + Windows 内核）。"""
    return (sys.platform or "").startswith(("cygwin", "msys"))


def is_windows_like() -> bool:
    """当前环境是否能调用 Win32 API（原生 Windows / Cygwin / MSYS2）。"""
    return os.name == "nt" or is_cygwin_like()


def to_winpid(pid: int, *, retries: int = 0,
              interval: float = 0.02) -> int | None:
    """把进程号换算成 Windows PID。

    - 原生 Windows：pid 本身即 Windows PID，原样返回；
    - Cygwin / MSYS2：读 ``/proc/<pid>/winpid``（POSIX PID → Windows PID）；
      进程刚创建时该文件可能尚未出现，``retries`` 可做短暂重试；
    - 其它平台：返回 ``None``。

    Args:
        pid: 进程号（Cygwin 下为 POSIX pid）。
        retries: 读取失败后的重试次数（默认 0）。
        interval: 每次重试前的等待秒数。

    Returns:
        Windows PID；无法换算返回 ``None``。
    """
    if not _valid_pid(pid):
        return None
    if os.name == "nt":
        return int(pid)
    if not is_cygwin_like():
        return None
    attempts = max(0, int(retries)) + 1
    for index in range(attempts):
        try:
            with open(f"/proc/{int(pid)}/winpid", encoding="utf-8") as handle:
                value = int(handle.read().strip())
            if value > 0:
                return value
        except (OSError, ValueError):
            pass
        if index + 1 < attempts:
            time.sleep(max(0.0, float(interval)))
    return None


def _last_error() -> int:
    """读取最近一次 Win32 调用的错误码（Cygwin 下回退 ``GetLastError``）。"""
    try:
        return int(ctypes.get_last_error())
    except (AttributeError, OSError):
        pass
    try:
        return int(ctypes.GetLastError())
    except (AttributeError, OSError):
        return 0


class PROCESSENTRY32W(ctypes.Structure):
    """``PROCESSENTRY32W``（固定宽度字段：LLP64 下布局正确）。"""

    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class FILETIME(ctypes.Structure):
    """``FILETIME``（100ns 计数，1601-01-01 起）。"""

    _fields_ = [
        ("dwLowDateTime", ctypes.c_uint32),
        ("dwHighDateTime", ctypes.c_uint32),
    ]


class LARGE_INTEGER(ctypes.Structure):
    """``LARGE_INTEGER``（联合体按 64 位整数访问）。"""

    _fields_ = [("QuadPart", ctypes.c_int64)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    """``JOBOBJECT_BASIC_ACCOUNTING_INFORMATION``（作业对象统计）。"""

    _fields_ = [
        ("TotalUserTime", LARGE_INTEGER),
        ("TotalKernelTime", LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", LARGE_INTEGER),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


#: kernel32 绑定缓存（懒加载；加载失败后不再重试）
_kernel32 = None
_load_failed = False


def _load_kernel32():
    """懒加载并绑定 kernel32 函数签名（非 Windows / 加载失败返回 None）。"""
    global _kernel32, _load_failed
    if _kernel32 is not None:
        return _kernel32
    if _load_failed or not is_windows_like():
        return None
    try:
        # 原生 Windows 用 WinDLL（stdcall + use_last_error）；Cygwin 的 ctypes
        # 没有 WinDLL，但 x86_64 只有一种调用约定，CDLL 可直接调用 Win32。
        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is not None:
            lib = win_dll("kernel32", use_last_error=True)
        else:
            # Cygwin 的 dlopen 需要完整 DLL 名（"kernel32" 会报 ENOENT）
            lib = ctypes.CDLL("kernel32.dll")
        lib.CreateToolhelp32Snapshot.argtypes = (ctypes.c_uint32, ctypes.c_uint32)
        lib.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        lib.Process32FirstW.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W))
        lib.Process32FirstW.restype = ctypes.c_int
        lib.Process32NextW.argtypes = (ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W))
        lib.Process32NextW.restype = ctypes.c_int
        lib.CloseHandle.argtypes = (ctypes.c_void_p,)
        lib.CloseHandle.restype = ctypes.c_int
        lib.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        lib.OpenProcess.restype = ctypes.c_void_p
        lib.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        lib.TerminateProcess.restype = ctypes.c_int
        lib.GetExitCodeProcess.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        )
        lib.GetExitCodeProcess.restype = ctypes.c_int
        lib.GetProcessTimes.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
            ctypes.POINTER(FILETIME),
        )
        lib.GetProcessTimes.restype = ctypes.c_int
        # 作业对象（Job Object）：Windows 上「杀整棵进程树」最可靠的机制——
        # 后代即使父进程已退出仍保留 Job 成员关系，TerminateJobObject
        # 能一次杀掉全部存活成员（Toolhelp32 的 PPID 链在中间层退出后会断）。
        lib.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        lib.CreateJobObjectW.restype = ctypes.c_void_p
        lib.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        lib.AssignProcessToJobObject.restype = ctypes.c_int
        lib.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        lib.TerminateJobObject.restype = ctypes.c_int
        lib.QueryInformationJobObject.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_void_p,
        )
        lib.QueryInformationJobObject.restype = ctypes.c_int
        # Job 成员判定（枚举 Job 内进程 -> 窗口归属 / 杀后补杀）
        lib.IsProcessInJob.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
        )
        lib.IsProcessInJob.restype = ctypes.c_int
    except (AttributeError, OSError):
        _load_failed = True
        logger.debug("kernel32 进程 API 绑定失败（非 Windows 或系统受限）", exc_info=True)
        return None
    _kernel32 = lib
    return lib


def is_supported() -> bool:
    """当前环境是否可用本模块（Windows 内核且 kernel32 API 绑定成功）。"""
    return _load_kernel32() is not None


def _valid_pid(pid) -> bool:
    """pid 是否为合法的正整数值（排除 bool / 0 / 负数 / 字符串）。"""
    return (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0)


def list_processes() -> list[tuple[int, int, str]]:
    """枚举系统进程：``[(pid, ppid, exe_name), ...]``。

    ``ppid`` 是进程**创建时**的父 PID（父进程退出后不变），因此可用来定位
    孤儿后代。非 Windows / 快照失败时返回空表。
    """
    lib = _load_kernel32()
    if lib is None:
        return []
    snapshot = lib.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE_VALUE:
        logger.debug("CreateToolhelp32Snapshot 失败")
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    result: list[tuple[int, int, str]] = []
    try:
        ok = lib.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            result.append((
                int(entry.th32ProcessID),
                int(entry.th32ParentProcessID),
                entry.szExeFile,
            ))
            ok = lib.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        lib.CloseHandle(snapshot)
    return result


def _exe_stem(name: str) -> str:
    """可执行名归一化：小写、去掉 ``.exe`` 后缀（取不到返回空串）。

    ★ Cygwin / MSYS2 下命令行通常写 ``mintty`` / ``python`` / ``sleep``
    （不带 ``.exe``，PATH 查找由 Cygwin 完成），而 Toolhelp32 的
    ``szExeFile`` 恒为 ``mintty.exe`` 这类带后缀的名字。若按字面精确比较，
    ``op=windows`` / ``screenshot`` / 输入 op 的 hint 兜底（见
    ``_screenshot.win.resolve_window_pids``）与 ``op=kill`` 的按名兜底就
    完全命中不了——Cygwin 下 GUI 程序 daemon 化（mintty 会 reparent 到
    init）后窗口无法归属到任务，实测 100% 复现。两侧统一剥掉后缀再比较。
    """
    text = (name or "").strip().lower()
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def find_processes_by_name(name: str) -> list[int]:
    """按可执行文件名（不区分大小写、忽略 ``.exe`` 后缀）返回存活 pid 列表。

    供 Cygwin / MSYS2 下 App Execution Alias（Store 应用）与 daemon 化
    GUI 程序（mintty 等）的窗口归属 / 杀树兜底：启动别名的实际进程可能
    不是任务根进程的后代，但 exe 名通常仍能对上。命令名带不带 ``.exe``
    都能匹配（见 :func:`_exe_stem`），不会误匹配 ``notepadplus.exe``
    这类仅前缀相同的名字。
    """
    target = _exe_stem(name)
    if not target:
        return []
    result: list[int] = []
    for pid, _ppid, exe in list_processes():
        if _exe_stem(exe) == target:
            result.append(int(pid))
    return result


def _open_process(pid: int, access: int):
    """打开进程句柄（失败返回 None；不区分错误原因，由调用方判断）。"""
    lib = _load_kernel32()
    if lib is None or not _valid_pid(pid):
        return None
    return lib.OpenProcess(access, 0, pid) or None


def process_create_time(pid: int) -> int | None:
    """读取进程创建时间（100ns 计数）；进程不存在 / 无权限返回 None。

    用于 pid 复用防护：后台任务启动时记下根进程的创建时间，杀树前比对——
    pid 已被系统复用时创建时间必然不同，据此拒绝杀（避免误杀无关进程树）。
    """
    handle = _open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if handle is None:
        return None
    lib = _load_kernel32()
    try:
        creation = FILETIME()
        exit_time = FILETIME()
        kernel_time = FILETIME()
        user_time = FILETIME()
        if not lib.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
    finally:
        lib.CloseHandle(handle)


def is_process_alive(pid: int) -> bool:
    """进程是否仍存活。

    - 句柄打开失败：``ERROR_ACCESS_DENIED`` 说明进程**存在但不可操作** →
      判存活（调用方据此如实报告「杀不掉」，而不是谎报「无残留」）；
      其它错误（进程不存在）→ 判死；
    - 句柄有效：``GetExitCodeProcess`` 为 ``STILL_ACTIVE`` → 存活，否则已退出。
    """
    lib = _load_kernel32()
    if lib is None or not _valid_pid(pid):
        return False
    handle = lib.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        if _last_error() == ERROR_ACCESS_DENIED:
            return True
        return False
    try:
        code = ctypes.c_uint32(0)
        if not lib.GetExitCodeProcess(handle, ctypes.byref(code)):
            # 查询失败极罕见（句柄刚失效等）：保守视为存活，不谎报已退出
            return True
        return int(code.value) == STILL_ACTIVE
    finally:
        lib.CloseHandle(handle)


def terminate_process(pid: int) -> bool:
    """强制终止进程（``TerminateProcess``，返回是否成功）。

    失败（进程已退出 / 权限不足 / 受保护进程）返回 False，由调用方决定
    是否回退 ``taskkill``。
    """
    handle = _open_process(pid, PROCESS_TERMINATE)
    if handle is None:
        return False
    lib = _load_kernel32()
    try:
        return bool(lib.TerminateProcess(handle, 1))
    finally:
        lib.CloseHandle(handle)


def is_pid_reused(pid: int, create_time: int | None) -> bool | None:
    """pid 是否已被系统复用（创建时间与记录不一致）。

    Args:
        pid: 待判断的 PID。
        create_time: 记录下来的进程创建时间（``process_create_time`` 的
            返回值）；空值时无法判断。

    Returns:
        True 表示 pid 已被复用；False 表示仍是最初那个进程；
        None 表示无法判断（未提供创建时间 / 进程已不存在 / 读取失败）。
    """
    if not create_time or not _valid_pid(pid):
        return None
    actual = process_create_time(pid)
    if actual is None:
        return None
    return actual != int(create_time)


# ── 作业对象（Job Object）：可靠的整树终止 ──────────────
#
# 为什么需要 Job：Toolhelp32 的父子关系是「创建时的父 PID」，中间层进程
# 退出后链条断裂——`根 shell → 子进程（已退出）→ 孙进程（存活）` 这种
# 多层孤儿从根 PID 出发**收集不到**（实测：bash 后台任务由 PowerShell 启动
# Python、Python 再启动常驻子进程，PowerShell 退出后孙进程残留）。Job 的
# 成员关系由内核维护，父进程退出不影响，TerminateJobObject 可一次杀光。
#
# 用法：创建进程后立刻 ``create_job`` + ``assign_process_to_job``，把句柄
# 随任务记录保存；kill / 中断时 ``terminate_job``，记录移除时
# ``close_handle``。Assign 失败（父进程受 Job 限制等）时调用方回退
# Toolhelp32 + taskkill。


def create_job() -> int | None:
    """创建匿名作业对象，返回句柄（失败 / 非 Windows 返回 None）。"""
    lib = _load_kernel32()
    if lib is None:
        return None
    handle = lib.CreateJobObjectW(None, None)
    return int(handle) if handle else None


def assign_process_to_job(job: int, pid: int) -> bool:
    """把 ``pid`` 加入作业对象（之后它启动的后代自动成为 Job 成员）。

    Returns:
        是否成功；失败时调用方应关闭 Job 句柄并回退其它树收集通道。
    """
    lib = _load_kernel32()
    if lib is None or not job or not _valid_pid(pid):
        return False
    handle = lib.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid)
    if not handle:
        return False
    try:
        return bool(lib.AssignProcessToJobObject(
            ctypes.c_void_p(int(job)), handle,
        ))
    finally:
        lib.CloseHandle(handle)


def terminate_job(job: int) -> bool:
    """终止作业对象内的全部进程（返回调用是否成功）。"""
    lib = _load_kernel32()
    if lib is None or not job:
        return False
    return bool(lib.TerminateJobObject(ctypes.c_void_p(int(job)), 1))


def job_active_processes(job: int) -> int | None:
    """作业对象内当前活动进程数（查询失败返回 None）。"""
    lib = _load_kernel32()
    if lib is None or not job:
        return None
    info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    ok = lib.QueryInformationJobObject(
        ctypes.c_void_p(int(job)),
        JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(JOBOBJECT_BASIC_ACCOUNTING_INFORMATION),
        None,
    )
    if not ok:
        return None
    return int(info.ActiveProcesses)


def job_processes(job: int) -> list[int]:
    """枚举作业对象内当前存活的 Windows pid（查询失败返回空表）。

    ``QueryInformationJobObject`` 只给活动进程数，拿不到 PID 列表；这里用
    ``JobObjectBasicProcessIdList``（信息类 3）一次取出全部成员 PID。Job
    内可能包含 ``Toolhelp32`` 父链已断开的孤儿后代，因此窗口归属 / 杀后
    补杀都以它为准。

    注：Cygwin 的 ``IsProcessInJob`` 实测返回 0（无法判定成员），因此不采用
    逐进程判定的方案。
    """
    lib = _load_kernel32()
    if lib is None or not job:
        return []
    header = 8  # NumberOfAssignedProcesses + NumberOfProcessIdsInList（各 DWORD）
    size = header + _JOB_PID_LIST_CAPACITY * ctypes.sizeof(ctypes.c_size_t)
    buffer = ctypes.create_string_buffer(size)
    ok = lib.QueryInformationJobObject(
        ctypes.c_void_p(int(job)),
        JOB_OBJECT_BASIC_PROCESS_ID_LIST,
        buffer,
        size,
        None,
    )
    if not ok:
        logger.debug("查询 Job 进程列表失败: job=%s err=%s", job, _last_error())
        return []
    count = int(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32))[1])
    if count <= 0:
        return []
    count = min(count, _JOB_PID_LIST_CAPACITY)
    array = (ctypes.c_size_t * count).from_buffer(buffer, header)
    return [int(pid) for pid in array if int(pid) > 0]


def close_handle(handle: int) -> None:
    """关闭内核句柄（Job / 进程句柄等；重复关闭 / 非法值静默忽略）。"""
    lib = _load_kernel32()
    if lib is None or not handle:
        return
    try:
        lib.CloseHandle(ctypes.c_void_p(int(handle)))
    except Exception:  # pragma: no cover - 依赖系统调用
        logger.debug("CloseHandle 失败: %s", handle, exc_info=True)


__all__ = [
    "assign_process_to_job",
    "close_handle",
    "create_job",
    "find_processes_by_name",
    "is_cygwin_like",
    "is_pid_reused",
    "is_process_alive",
    "is_supported",
    "is_windows_like",
    "job_active_processes",
    "job_processes",
    "list_processes",
    "process_create_time",
    "terminate_job",
    "terminate_process",
    "to_winpid",
]
