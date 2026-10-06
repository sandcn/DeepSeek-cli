"""Win32 API 绑定（ctypes）——截图后端、进程枚举与窗口输入注入共用。

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
import logging
import os
import sys
import threading
from ctypes import (
    POINTER,
    Structure,
    Union,
    byref,
    c_int32,
    c_size_t,
    c_ssize_t,
    c_uint16,
    c_uint32,
    c_void_p,
    c_wchar,
    c_wchar_p,
)
from typing import Any

logger = logging.getLogger(__name__)

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
SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_MAXIMIZE = 3
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOWMAXIMIZED = SW_MAXIMIZE
MAX_PATH = 260

# SetWindowPos 标志（窗口移动 / 缩放）
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

# 窗口消息（窗口控制路径）
WM_CLOSE = 0x0010

# ── 输入注入（鼠标 / 键盘）常量 ──────────────────────────
# 供窗口输入后端复用：SendInput 合成真实输入事件作用于前台窗口；
# PostMessage 直接投递窗口消息（不移动真实光标、不需要焦点）。
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# MapVirtualKeyW 的映射类型（补全扫描码，让目标程序拿到正确的 key code）
MAPVK_VK_TO_VSC = 0
MAPVK_VK_TO_VSC_EX = 4

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
WHEEL_DELTA = 120

# 系统度量：虚拟桌面范围（多显示器下归一化绝对坐标的基准）
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# 窗口消息（PostMessage 投递路径）
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MBUTTONDBLCLK = 0x0209
WM_MOUSEWHEEL = 0x020A
WM_MOUSEHWHEEL = 0x020E
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_SHIFT = 0x0004
MK_CONTROL = 0x0008
MK_MBUTTON = 0x0010

# 虚拟键码（窗口输入后端把规范化键名映射于此）
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_PAUSE = 0x13
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_APPS = 0x5D
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91

#: 功能键 VK 基址（VK_F1 = 0x70 … VK_F24 = 0x87）
VK_F1 = 0x70
VK_F24 = 0x87

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


class POINT(Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", c_uint16),
        ("wScan", c_uint16),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(Structure):
    _fields_ = [
        ("uMsg", DWORD),
        ("wParamL", c_uint16),
        ("wParamH", c_uint16),
    ]


class _INPUTUNION(Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(Structure):
    """``INPUT`` 结构（联合体通过 ``_anonymous_`` 暴露为字段）。"""

    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


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
    lib.SetWindowPos.argtypes = [HWND, HWND, c_int32, c_int32, c_int32, c_int32, UINT]
    lib.SetWindowPos.restype = BOOL
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
    # 输入注入 / 窗口消息投递 API
    lib.SendInput.argtypes = [UINT, POINTER(INPUT), c_int32]
    lib.SendInput.restype = UINT
    lib.PostMessageW.argtypes = [HWND, UINT, ULONG_PTR, c_ssize_t]
    lib.PostMessageW.restype = BOOL
    lib.GetForegroundWindow.argtypes = []
    lib.GetForegroundWindow.restype = HWND
    lib.SetActiveWindow.argtypes = [HWND]
    lib.SetActiveWindow.restype = HWND
    lib.SetFocus.argtypes = [HWND]
    lib.SetFocus.restype = HWND
    lib.ClientToScreen.argtypes = [HWND, POINTER(POINT)]
    lib.ClientToScreen.restype = BOOL
    lib.ScreenToClient.argtypes = [HWND, POINTER(POINT)]
    lib.ScreenToClient.restype = BOOL
    lib.GetClientRect.argtypes = [HWND, POINTER(RECT)]
    lib.GetClientRect.restype = BOOL
    lib.GetSystemMetrics.argtypes = [c_int32]
    lib.GetSystemMetrics.restype = c_int32
    lib.WindowFromPoint.argtypes = [POINT]
    lib.WindowFromPoint.restype = HWND
    lib.VkKeyScanW.argtypes = [c_uint16]
    lib.VkKeyScanW.restype = c_int32
    lib.MapVirtualKeyW.argtypes = [UINT, UINT]
    lib.MapVirtualKeyW.restype = UINT
    lib.GetAncestor.argtypes = [HWND, UINT]
    lib.GetAncestor.restype = HWND
    lib.AttachThreadInput.argtypes = [DWORD, DWORD, BOOL]
    lib.AttachThreadInput.restype = BOOL
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
    lib.GetCurrentThreadId.argtypes = []
    lib.GetCurrentThreadId.restype = DWORD
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


def show_window(hwnd, command: int) -> bool:
    """执行 ``ShowWindow`` 命令（还原 / 最小化 / 最大化 / 显示）。

    Args:
        hwnd: 窗口句柄。
        command: ``SW_*`` 常量（如 ``SW_RESTORE`` / ``SW_MAXIMIZE``）。

    Returns:
        调用是否成功（窗口状态是否实际改变由系统决定）。
    """
    try:
        return bool(user32().ShowWindow(hwnd, int(command)))
    except OSError:
        return False


def set_window_pos(hwnd, x: int, y: int, width: int, height: int,
                   flags: int = SWP_NOZORDER | SWP_NOACTIVATE) -> bool:
    """移动 / 缩放窗口（``SetWindowPos``，坐标为屏幕像素）。"""
    try:
        return bool(user32().SetWindowPos(
            hwnd, None, int(x), int(y), int(width), int(height), int(flags)
        ))
    except OSError:
        return False


def is_window_visible(hwnd) -> bool:
    """窗口是否可见（``IsWindowVisible``）。"""
    try:
        return bool(user32().IsWindowVisible(hwnd))
    except OSError:
        return False


def is_window_minimized(hwnd) -> bool:
    """窗口是否已最小化（``IsIconic``）。"""
    try:
        return bool(user32().IsIconic(hwnd))
    except OSError:
        return False


def is_window(hwnd) -> bool:
    """句柄是否为仍然存在的窗口（``IsWindow``）。"""
    try:
        return bool(user32().IsWindow(hwnd))
    except OSError:
        return False


def close_window(hwnd) -> bool:
    """请求窗口关闭（投递 ``WM_CLOSE``，等价于点标题栏关闭按钮）。"""
    return post_message(hwnd, WM_CLOSE)


def map_virtual_key(vk: int, map_type: int = MAPVK_VK_TO_VSC_EX) -> int:
    """把虚拟键码映射为扫描码（失败返回 0）。

    ``MAPVK_VK_TO_VSC_EX`` 对扩展键返回带 ``0xE000`` 前缀的扫描码，
    调用方可据此设置 ``KEYEVENTF_EXTENDEDKEY``；合成键盘事件时带上扫描码，
    目标程序（浏览器、游戏、DirectInput 程序）才能得到正确的物理键信息。
    """
    if not vk:
        return 0
    user = user32()
    func = getattr(user, "MapVirtualKeyW", None)
    if func is None:  # pragma: no cover - 旧系统
        return 0
    try:
        return int(func(int(vk), int(map_type))) & 0xFFFF
    except OSError:  # pragma: no cover - 依赖系统调用
        return 0


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


# ── 输入注入 ────────────────────────────────────────────
#
# 两条投递路径：
#   - SendInput：合成系统级输入事件，作用于**前台窗口**（调用方需先激活目标
#     窗口），兼容性最好（鼠标/键盘/D3D 游戏、IME 均可）；
#   - PostMessage：把 WM_* 消息直接投递给目标窗口，不需要焦点、不移动真实
#     光标，但依赖目标程序处理这些消息（部分自绘/游戏窗口会忽略）。


def mouse_input(flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> INPUT:
    """构造一条鼠标 INPUT 事件。"""
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi = MOUSEINPUT(
        dx=int(dx), dy=int(dy), mouseData=int(mouse_data),
        dwFlags=int(flags), time=0, dwExtraInfo=0,
    )
    return item


def key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    """构造一条键盘 INPUT 事件（虚拟键码 / 扫描码 / Unicode 三选一）。"""
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(
        wVk=int(vk), wScan=int(scan), dwFlags=int(flags), time=0, dwExtraInfo=0,
    )
    return item


def unicode_key_input(code_unit: int, *, key_up: bool = False) -> INPUT:
    """构造一条 Unicode 文本输入事件（不依赖键盘布局，可输入任意码元）。"""
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    return key_input(vk=0, scan=int(code_unit), flags=flags)


def send_inputs(items: list[INPUT]) -> int:
    """批量投递 INPUT 事件，返回系统实际接收的事件数。"""
    if not items:
        return 0
    array = (INPUT * len(items))(*items)
    return int(user32().SendInput(len(items), array, ctypes.sizeof(INPUT)))


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """虚拟桌面矩形 ``(left, top, width, height)``（多显示器合并范围）。"""
    user = user32()
    left = int(user.GetSystemMetrics(SM_XVIRTUALSCREEN))
    top = int(user.GetSystemMetrics(SM_YVIRTUALSCREEN))
    width = int(user.GetSystemMetrics(SM_CXVIRTUALSCREEN))
    height = int(user.GetSystemMetrics(SM_CYVIRTUALSCREEN))
    if width <= 0 or height <= 0:  # 度量异常时回退主屏尺寸
        left = top = 0
        width = max(int(user.GetSystemMetrics(SM_CXSCREEN)), 1)
        height = max(int(user.GetSystemMetrics(SM_CYSCREEN)), 1)
    return left, top, width, height


def normalize_absolute(x: int, y: int) -> tuple[int, int]:
    """把屏幕像素坐标换算为 ``MOUSEEVENTF_ABSOLUTE`` 要求的 0..65535 归一化值。"""
    left, top, width, height = virtual_screen_rect()
    if width <= 1 or height <= 1:
        raise OSError(f"虚拟桌面尺寸异常: {width}x{height}")
    nx = round((int(x) - left) * 65535 / (width - 1))
    ny = round((int(y) - top) * 65535 / (height - 1))
    clamp = lambda value: min(65535, max(0, value))  # noqa: E731
    return clamp(nx), clamp(ny)


def client_origin(hwnd) -> tuple[int, int] | None:
    """客户区左上角的屏幕坐标（失败返回 None）。"""
    point = POINT(0, 0)
    if not user32().ClientToScreen(hwnd, byref(point)):
        return None
    return int(point.x), int(point.y)


def client_size(hwnd) -> tuple[int, int]:
    """客户区尺寸 ``(width, height)``（失败返回 ``(0, 0)``）。"""
    rect = RECT()
    if not user32().GetClientRect(hwnd, byref(rect)):
        return 0, 0
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def hwnd_value(hwnd) -> int:
    """把窗口句柄统一取为整数（int / ctypes 句柄对象都可）。"""
    if hwnd is None:
        return 0
    if isinstance(hwnd, int):
        return hwnd
    value = getattr(hwnd, "value", None)
    return int(value) if value else 0


def foreground_window():
    """当前前台窗口句柄（无则 None）。"""
    return user32().GetForegroundWindow()


def is_foreground(hwnd) -> bool:
    """``hwnd`` 是否为当前前台窗口（无法判定时返回 False）。"""
    target = hwnd_value(hwnd)
    if not target:
        return False
    try:
        current = hwnd_value(user32().GetForegroundWindow())
    except OSError:
        return False
    return current == target


def foreground_description() -> str:
    """当前前台窗口的一行描述（读取失败时返回「未知」）。

    「无法把目标窗口置于前台」这类错误需要告诉调用方**是谁占着前台**：
    Windows 的前台锁定策略会拒绝后台进程的 ``SetForegroundWindow``，
    只有知道抢占者（如全屏游戏）才能决定是关掉它、还是改用消息投递通道。
    """
    try:
        handle = foreground_window()
    except OSError:  # pragma: no cover - 依赖系统调用
        return "未知"
    value = hwnd_value(handle)
    if not value:
        return "未知（无前台窗口）"
    try:
        title = window_text(handle)
        class_name = window_class(handle)
    except OSError:  # pragma: no cover - 依赖系统调用
        return f"handle=0x{value:X}"
    return f"{title or '无标题'}（class={class_name}, handle=0x{value:X}）"


def post_message(hwnd, msg: int, wparam: int = 0, lparam: int = 0) -> bool:
    """把窗口消息投递给 ``hwnd``（不等待处理，返回是否入队成功）。"""
    return bool(user32().PostMessageW(hwnd, int(msg), int(wparam), int(lparam)))


def key_scan_code(char: str) -> tuple[int, int] | None:
    """把字符映射为 ``(虚拟键码, 需要的隐式修饰状态)``。

    返回的第二个元素为 ``VkKeyScanW`` 的 shift 状态位：
    ``1 = shift``、``2 = ctrl``、``4 = alt``（按位与判断）。
    字符无法映射（当前键盘布局没有该键）时返回 ``None``。
    """
    if not char:
        return None
    code_unit = ord(char[0])
    if code_unit > 0xFFFF:
        return None
    scanned = int(user32().VkKeyScanW(code_unit))
    if scanned == -1:
        return None
    return scanned & 0xFF, (scanned >> 8) & 0xFF


# ── 窗口层次与前台激活 ───────────────────────────────────

#: ``GetAncestor`` 的 ``gaFlags``：取顶层（根）祖先
GA_ROOT = 2


def window_root(hwnd) -> int:
    """返回窗口的顶层祖先句柄（失败返回原句柄值）。"""
    value = hwnd_value(hwnd)
    if not value:
        return 0
    try:
        root = hwnd_value(user32().GetAncestor(hwnd, GA_ROOT))
    except OSError:  # pragma: no cover - 依赖系统调用
        return value
    return root or value


def window_from_point(screen_x: int, screen_y: int) -> int:
    """返回屏幕点下的窗口句柄（含子控件；无则 0）。"""
    try:
        return hwnd_value(user32().WindowFromPoint(POINT(int(screen_x), int(screen_y))))
    except OSError:  # pragma: no cover - 依赖系统调用
        return 0


def hit_test_window(root_hwnd, screen_x: int, screen_y: int) -> int:
    """返回屏幕点下**属于 ``root_hwnd`` 窗口树**的最深窗口。

    直接投递窗口消息（PostMessage）时，鼠标消息必须发给真正的子控件
    （Edit / Button 等），否则对话框类程序不会响应；本函数先取该点下的
    窗口，再校验其顶层祖先是否为 ``root_hwnd``，命中则返回子控件，
    否则回退 ``root_hwnd``。
    """
    root = hwnd_value(root_hwnd)
    hit = window_from_point(screen_x, screen_y)
    if not hit:
        return root
    if hit == root or window_root(hit) == root:
        return hit
    return root


def current_thread_id() -> int:
    """当前线程 ID。"""
    return int(kernel32().GetCurrentThreadId())


def window_thread_id(hwnd) -> int:
    """窗口所属线程 ID（无归属返回 0）。"""
    thread_id = DWORD()
    # GetWindowThreadProcessId 的返回值即线程 ID，进程 ID 经第二参输出
    value = int(user32().GetWindowThreadProcessId(hwnd, byref(thread_id)))
    return value


def set_foreground(hwnd) -> bool:
    """尽最大努力把窗口激活为前台（AttachThreadInput 技巧 + 常规提升）。

    Windows 前台锁定策略会拒绝后台进程的 ``SetForegroundWindow``；把本
    线程的输入队列临时附加到当前前台窗口线程与目标窗口线程后，调用通常
    可以成功。无论是否成功都恢复附加状态，并返回最终是否成为前台窗口。
    """
    target = hwnd_value(hwnd)
    if not target:
        return False
    if is_foreground(target):
        return True
    user = user32()
    current = current_thread_id()
    attached: list[int] = []
    try:
        for thread in (window_thread_id(foreground_window()), window_thread_id(target)):
            if thread and thread != current:
                try:
                    if user.AttachThreadInput(current, thread, 1):
                        attached.append(thread)
                except OSError:  # pragma: no cover - 依赖系统调用
                    logger.debug("AttachThreadInput 失败 thread=%s", thread)
        raise_window(target)
        try:
            user.SetForegroundWindow(target)
        except OSError:  # pragma: no cover - 依赖系统调用
            logger.debug("SetForegroundWindow 失败 hwnd=%s", target)
    finally:
        for thread in attached:
            try:
                user.AttachThreadInput(current, thread, 0)
            except OSError:  # pragma: no cover - 依赖系统调用
                pass
    return is_foreground(target)
