"""Win32 API 绑定（ctypes）——截图后端与进程枚举共用。

**为什么不用 ``ctypes.wintypes``**：Cygwin（LP64）下 ``wintypes.LONG`` 是
8 字节的 ``c_long``，与 Windows ABI（LLP64，``LONG`` 为 4 字节）不符，
直接使用会导致 ``GetWindowRect`` 等结构体字段错位、读出乱码坐标。
本模块统一用固定宽度类型（``c_int32`` / ``c_uint32`` / ``c_size_t``）
声明 Win32 结构，原生 Windows 与 Cygwin/MSYS2 下都可正确工作。

**库加载**：原生 Windows 用 ``ctypes.WinDLL``（stdcall）；Cygwin/MSYS2 的
ctypes 没有 ``WinDLL``/``WINFUNCTYPE``，但 x86_64 只有一种调用约定，
``ctypes.CDLL`` + ``CFUNCTYPE`` 可直接调用 Win32 API（Windows 上两者等价）。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_int32,
    c_size_t,
    c_uint16,
    c_uint32,
    c_void_p,
    c_wchar,
    c_wchar_p,
)
from typing import Any

BOOL = c_int32
DWORD = c_uint32
UINT = c_uint32
LONG = c_int32
ULONG_PTR = c_size_t
HANDLE = c_void_p
HWND = c_void_p
HDC = c_void_p
HBITMAP = c_void_p

#: 回调类型工厂：原生 Windows 用 WINFUNCTYPE（stdcall），Cygwin 回退 CFUNCTYPE
CALLBACK = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)

#: 无效句柄值（HANDLE）-1
INVALID_HANDLE_VALUE = c_void_p(-1).value

# ── 常量 ────────────────────────────────────────────────
PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
TH32CS_SNAPPROCESS = 0x00000002
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_RESTORE = 9
MAX_PATH = 260

# ── DPI 感知 ────────────────────────────────────────────
#: 进程 DPI 感知级别（``GetProcessDpiAwareness`` 返回值）
PROCESS_DPI_AWARENESS_UNAWARE = 0
PROCESS_DPI_AWARENESS_SYSTEM_AWARE = 1
PROCESS_DPI_AWARENESS_PER_MONITOR_AWARE = 2
#: ``shcore!SetProcessDpiAwareness`` 的每显示器感知级别
PROCESS_PER_MONITOR_DPI_AWARE = 2
#: ``user32!SetProcessDpiAwarenessContext`` 的每显示器感知 V2 上下文（(HANDLE)-4）
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


def is_windows_platform() -> bool:
    """当前解释器运行在 Windows 上（原生 Windows / Cygwin / MSYS2）。"""
    if os.name == "nt":
        return True
    return (sys.platform or "").startswith(("cygwin", "msys"))


def is_cygwin_like() -> bool:
    """当前解释器为 Cygwin/MSYS2（POSIX 层，pid 需映射到 Windows pid）。"""
    return (sys.platform or "").startswith(("cygwin", "msys"))


def load_library(name: str):
    """加载系统 DLL：原生 Windows 用 WinDLL（stdcall），Cygwin 用 CDLL。"""
    if hasattr(ctypes, "WinDLL"):
        return ctypes.WinDLL(name)
    return ctypes.CDLL(name)


# ── DPI 感知 ────────────────────────────────────────────
#
# 为什么截图前必须让进程 DPI 感知：非感知进程在高 DPI（如 150%）显示器上
# 调用 ``GetWindowRect`` 得到的是被系统虚拟化缩小的坐标（如 2560 物理像素
# 的屏幕读成 1707），而 ``PrintWindow`` / ``BitBlt`` 输出的始终是物理像素。
# 按缩小后的尺寸创建内存 DC，只能容纳整窗左上角的一部分，产物右下角被裁掉。
# 把进程标记为 DPI 感知后，窗口几何与像素 1:1 对应，不再截断。


def process_dpi_awareness() -> "int | None":
    """读取当前进程 DPI 感知级别。

    Returns:
        ``0`` unaware / ``1`` system / ``2`` per-monitor；无法读取（非
        Windows 或缺少 ``shcore`` 导出）时返回 ``None``。
    """
    if not is_windows_platform():
        return None
    try:
        lib = load_library("shcore.dll")
    except OSError:
        return None
    func = getattr(lib, "GetProcessDpiAwareness", None)
    if func is None:
        return None
    func.argtypes = [HANDLE, POINTER(c_int32)]
    func.restype = c_int32  # HRESULT
    value = c_int32(PROCESS_DPI_AWARENESS_UNAWARE)
    try:
        if int(func(None, byref(value))) < 0:
            return None
    except OSError:
        return None
    return int(value.value)


def _set_dpi_awareness_context() -> bool:
    """Win10 1703+：``SetProcessDpiAwarenessContext``（每显示器 V2）。"""
    if _SET_DPI_AWARENESS_CONTEXT is None:
        user32()
    if _SET_DPI_AWARENESS_CONTEXT is None:
        return False
    try:
        return bool(
            _SET_DPI_AWARENESS_CONTEXT(
                c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
            )
        )
    except OSError:
        return False


def _set_shcore_dpi_awareness() -> bool:
    """Win8.1+：``shcore!SetProcessDpiAwareness``（每显示器感知）。"""
    try:
        lib = load_library("shcore.dll")
    except OSError:
        return False
    func = getattr(lib, "SetProcessDpiAwareness", None)
    if func is None:
        return False
    func.argtypes = [c_int32]
    func.restype = c_int32  # HRESULT
    try:
        return int(func(PROCESS_PER_MONITOR_DPI_AWARE)) >= 0
    except OSError:
        return False


def _set_system_dpi_aware() -> bool:
    """Vista+：``SetProcessDPIAware``（系统级感知，单显示器高 DPI 足够）。"""
    if _SET_SYSTEM_DPI_AWARE is None:
        user32()
    if _SET_SYSTEM_DPI_AWARE is None:
        return False
    try:
        return bool(_SET_SYSTEM_DPI_AWARE())
    except OSError:
        return False


#: DPI 感知设置尝试顺序：新接口优先（每显示器 > 系统级）
_DPI_AWARE_SETTERS = (
    _set_dpi_awareness_context,
    _set_shcore_dpi_awareness,
    _set_system_dpi_aware,
)


def _apply_dpi_awareness() -> bool:
    """真正执行一次 DPI 感知设置，返回最终是否处于感知状态。"""
    if not is_windows_platform():
        return False
    current = process_dpi_awareness()
    if current is not None and current != PROCESS_DPI_AWARENESS_UNAWARE:
        return True
    for setter in _DPI_AWARE_SETTERS:
        try:
            if setter():
                return True
        except (OSError, AttributeError):  # pragma: no cover - 依赖系统调用
            continue
    result = process_dpi_awareness()
    return bool(result) if result is not None else False


def ensure_process_dpi_aware() -> bool:
    """确保当前进程为 DPI 感知（幂等，线程安全）。

    返回最终是否处于 DPI 感知状态。设置失败（如服务会话、系统限制）时
    返回 False，调用方仍可继续截图（只是高 DPI 下几何可能被系统虚拟化）。
    """
    global _DPI_AWARE
    with _DPI_LOCK:
        if _DPI_AWARE is None:
            _DPI_AWARE = _apply_dpi_awareness()
        return _DPI_AWARE


class RECT(Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class BITMAPINFOHEADER(Structure):
    _fields_ = [
        ("biSize", DWORD),
        ("biWidth", LONG),
        ("biHeight", LONG),
        ("biPlanes", c_uint16),
        ("biBitCount", c_uint16),
        ("biCompression", DWORD),
        ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG),
        ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD),
        ("biClrImportant", DWORD),
    ]


class BITMAPINFO(Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", DWORD * 1)]


class PROCESSENTRY32W(Structure):
    """``PROCESSENTRY32W``（th32DefaultHeapID 的 ULONG_PTR 对齐由 ctypes 自动处理）。"""

    _fields_ = [
        ("dwSize", DWORD),
        ("cntUsage", DWORD),
        ("th32ProcessID", DWORD),
        ("th32DefaultHeapID", ULONG_PTR),
        ("th32ModuleID", DWORD),
        ("cntThreads", DWORD),
        ("th32ParentProcessID", DWORD),
        ("pcPriClassBase", LONG),
        ("dwFlags", DWORD),
        ("szExeFile", c_wchar * MAX_PATH),
    ]


# ── 库句柄（懒加载并绑定签名） ───────────────────────────

_LIBS: dict = {}

#: 读取窗口扩展样式（GetWindowLongPtrW / GetWindowLongW，二选一后缓存）
_GET_WINDOW_LONG: Any = None

#: ``IsHungAppWindow`` 绑定（旧系统无此导出时为 None）
_IS_HUNG_APP_WINDOW: Any = None

#: ``SetProcessDpiAwarenessContext`` 绑定（Win10 1703+，旧系统为 None）
_SET_DPI_AWARENESS_CONTEXT: Any = None

#: ``SetProcessDPIAware`` 绑定（Vista+，旧系统为 None）
_SET_SYSTEM_DPI_AWARE: Any = None

#: DPI 感知设置的进程级缓存与串行锁（设置不可逆，幂等生效）
_DPI_LOCK = threading.Lock()
_DPI_AWARE: "bool | None" = None


def user32():
    lib = _LIBS.get("user32")
    if lib is not None:
        return lib
    lib = load_library("user32.dll")
    lib.GetWindowThreadProcessId.argtypes = [HWND, POINTER(DWORD)]
    lib.GetWindowThreadProcessId.restype = DWORD
    lib.EnumWindows.argtypes = [CALLBACK(BOOL, HWND, c_void_p), c_void_p]
    lib.EnumWindows.restype = BOOL
    lib.IsWindowVisible.argtypes = [HWND]
    lib.IsWindowVisible.restype = BOOL
    lib.IsIconic.argtypes = [HWND]
    lib.IsIconic.restype = BOOL
    lib.IsWindow.argtypes = [HWND]
    lib.IsWindow.restype = BOOL
    hung = getattr(lib, "IsHungAppWindow", None)
    if hung is not None:
        hung.argtypes = [HWND]
        hung.restype = BOOL
    global _IS_HUNG_APP_WINDOW
    _IS_HUNG_APP_WINDOW = hung
    # DPI 感知 API：Win10 1703+ 的上下文接口与 Vista+ 的系统级接口
    context_setter = getattr(lib, "SetProcessDpiAwarenessContext", None)
    if context_setter is not None:
        context_setter.argtypes = [c_void_p]
        context_setter.restype = BOOL
    global _SET_DPI_AWARENESS_CONTEXT, _SET_SYSTEM_DPI_AWARE
    _SET_DPI_AWARENESS_CONTEXT = context_setter
    system_setter = getattr(lib, "SetProcessDPIAware", None)
    if system_setter is not None:
        system_setter.argtypes = []
        system_setter.restype = BOOL
    _SET_SYSTEM_DPI_AWARE = system_setter
    lib.GetWindowTextLengthW.argtypes = [HWND]
    lib.GetWindowTextLengthW.restype = c_int32
    lib.GetWindowTextW.argtypes = [HWND, c_wchar_p, c_int32]
    lib.GetWindowTextW.restype = c_int32
    lib.GetWindowRect.argtypes = [HWND, POINTER(RECT)]
    lib.GetWindowRect.restype = BOOL
    lib.GetWindowDC.argtypes = [HWND]
    lib.GetWindowDC.restype = HDC
    lib.ReleaseDC.argtypes = [HWND, HDC]
    lib.ReleaseDC.restype = c_int32
    lib.PrintWindow.argtypes = [HWND, HDC, UINT]
    lib.PrintWindow.restype = BOOL
    lib.GetClassNameW.argtypes = [HWND, c_wchar_p, c_int32]
    lib.GetClassNameW.restype = c_int32
    lib.ShowWindow.argtypes = [HWND, c_int32]
    lib.ShowWindow.restype = BOOL
    lib.BringWindowToTop.argtypes = [HWND]
    lib.BringWindowToTop.restype = BOOL
    lib.SetForegroundWindow.argtypes = [HWND]
    lib.SetForegroundWindow.restype = BOOL
    # GetWindowLongPtrW 仅 64 位导出，32 位用 GetWindowLongW（同语义）
    long_ptr_name = "GetWindowLongPtrW" if ctypes.sizeof(c_void_p) == 8 else "GetWindowLongW"
    get_long = getattr(lib, long_ptr_name)
    get_long.argtypes = [HWND, c_int32]
    get_long.restype = c_size_t
    global _GET_WINDOW_LONG
    _GET_WINDOW_LONG = get_long
    _LIBS["user32"] = lib
    return lib


def gdi32():
    lib = _LIBS.get("gdi32")
    if lib is not None:
        return lib
    lib = load_library("gdi32.dll")
    lib.CreateCompatibleDC.argtypes = [HDC]
    lib.CreateCompatibleDC.restype = HDC
    lib.DeleteDC.argtypes = [HDC]
    lib.DeleteDC.restype = BOOL
    lib.CreateDIBSection.argtypes = [
        HDC, POINTER(BITMAPINFO), UINT, POINTER(c_void_p), HANDLE, DWORD,
    ]
    lib.CreateDIBSection.restype = HBITMAP
    lib.SelectObject.argtypes = [HDC, HBITMAP]
    lib.SelectObject.restype = HBITMAP
    lib.DeleteObject.argtypes = [HBITMAP]
    lib.DeleteObject.restype = BOOL
    lib.BitBlt.argtypes = [HDC, c_int32, c_int32, c_int32, c_int32, HDC, c_int32, c_int32, DWORD]
    lib.BitBlt.restype = BOOL
    _LIBS["gdi32"] = lib
    return lib


def kernel32():
    lib = _LIBS.get("kernel32")
    if lib is not None:
        return lib
    lib = load_library("kernel32.dll")
    lib.CreateToolhelp32Snapshot.argtypes = [DWORD, DWORD]
    lib.CreateToolhelp32Snapshot.restype = HANDLE
    lib.Process32FirstW.argtypes = [HANDLE, POINTER(PROCESSENTRY32W)]
    lib.Process32FirstW.restype = BOOL
    lib.Process32NextW.argtypes = [HANDLE, POINTER(PROCESSENTRY32W)]
    lib.Process32NextW.restype = BOOL
    lib.CloseHandle.argtypes = [HANDLE]
    lib.CloseHandle.restype = BOOL
    _LIBS["kernel32"] = lib
    return lib


def dwmapi():
    lib = _LIBS.get("dwmapi")
    if lib is not None:
        return lib
    lib = load_library("dwmapi.dll")
    lib.DwmGetWindowAttribute.argtypes = [HWND, DWORD, c_void_p, DWORD]
    lib.DwmGetWindowAttribute.restype = c_int32
    _LIBS["dwmapi"] = lib
    return lib


def window_text(hwnd) -> str:
    """读取窗口标题（失败返回空串）。"""
    length = user32().GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32().GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_class(hwnd) -> str:
    """读取窗口类名（失败返回空串）。"""
    buf = ctypes.create_unicode_buffer(256)
    if user32().GetClassNameW(hwnd, buf, 256) <= 0:
        return ""
    return buf.value


def window_is_toolwindow(hwnd) -> bool:
    """窗口是否声明为工具窗口（WS_EX_TOOLWINDOW，通常不出现在任务栏）。"""
    if _GET_WINDOW_LONG is None:
        user32()
    try:
        style = int(_GET_WINDOW_LONG(hwnd, GWL_EXSTYLE))
    except (OSError, TypeError):
        return False
    return bool(style & WS_EX_TOOLWINDOW)


def window_is_hung(hwnd) -> bool:
    """窗口是否无响应（``IsHungAppWindow`` 不可用时按「响应正常」处理）。"""
    if _IS_HUNG_APP_WINDOW is None:
        user32()
    if _IS_HUNG_APP_WINDOW is None:
        return False
    try:
        return bool(_IS_HUNG_APP_WINDOW(hwnd))
    except OSError:
        return False


def raise_window(hwnd) -> bool:
    """尽力把窗口提到最前（最小化则先还原），供屏幕拷贝（BitBlt）取到内容。

    仅提升 Z 序与可见性，不保证获得焦点；返回是否至少成功执行一次调用。
    """
    user = user32()
    ok = False
    try:
        if user.IsIconic(hwnd):
            user.ShowWindow(hwnd, SW_RESTORE)
            ok = True
        if user.BringWindowToTop(hwnd):
            ok = True
        user.SetForegroundWindow(hwnd)
    except OSError:
        return ok
    return ok


def window_pid(hwnd) -> int:
    """读取窗口所属的 Windows 进程 ID（无归属返回 0）。"""
    pid = DWORD()
    user32().GetWindowThreadProcessId(hwnd, byref(pid))
    return int(pid.value)


def window_rect(hwnd) -> tuple[int, int, int, int]:
    """读取窗口矩形 (left, top, right, bottom)，失败返回全 0。"""
    rect = RECT()
    if not user32().GetWindowRect(hwnd, byref(rect)):
        return (0, 0, 0, 0)
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def is_window_cloaked(hwnd) -> bool:
    """是否被 DWM 隐藏（UWP 幽灵窗口）：Cloaked 非 0 时不参与截图。"""
    try:
        value = DWORD()
        hr = dwmapi().DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, byref(value), ctypes.sizeof(DWORD)
        )
        return hr == 0 and value.value != 0
    except OSError:
        return False


def extended_frame_bounds(hwnd) -> "tuple[int, int, int, int] | None":
    """读取窗口的 DWM 可见边界 ``(left, top, right, bottom)``（屏幕像素坐标）。

    ``GetWindowRect`` 在 Win10 上包含系统为阴影/调整大小预留的**不可见边框**
    （非最大化窗口通常在左、上、右、下各约 7 个逻辑像素），``PrintWindow`` /
    ``BitBlt`` 对该区域无内容可渲染，截图四边因此出现黑边。DWM 扩展框边界
    给出真实可见区域，两者之差即需裁掉的偏移。

    Returns:
        可见边界；窗口非 DWM 合成、调用失败或旧系统无 ``dwmapi`` 时返回
        ``None``（调用方应回退到窗口矩形）。
    """
    try:
        rect = RECT()
        hr = dwmapi().DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, byref(rect), ctypes.sizeof(RECT)
        )
    except OSError:
        return None
    if hr != 0:
        return None
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def enum_children_windows() -> list:
    """枚举所有顶层窗口句柄。"""
    handles: list = []
    callback_type = CALLBACK(BOOL, HWND, c_void_p)

    def _cb(hwnd, _lparam):
        handles.append(hwnd)
        return 1

    ref = callback_type(_cb)  # 保持引用，防止回调被 GC
    user32().EnumWindows(ref, None)
    return handles


def list_processes() -> list[tuple[int, int, str]]:
    """枚举系统进程：``[(pid, ppid, exe_name), ...]``（非 Windows 返回空表）。"""
    kernel = kernel32()
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        return []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    result: list[tuple[int, int, str]] = []
    try:
        ok = kernel.Process32FirstW(snapshot, byref(entry))
        while ok:
            result.append((
                int(entry.th32ProcessID),
                int(entry.th32ParentProcessID),
                entry.szExeFile,
            ))
            ok = kernel.Process32NextW(snapshot, byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result
