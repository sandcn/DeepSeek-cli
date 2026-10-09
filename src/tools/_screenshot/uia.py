"""Windows UI Automation（UIA）控件枚举后端（ctypes COM，零第三方依赖）。

``EnumChildWindows`` 只能拿到**标准 Win32 子窗口**；Chrome / Electron / Qt /
WPF / UWP 等自绘界面的控件不是子窗口，枚举结果为空。UI Automation 是
Windows 无障碍框架：它把整棵控件树（含自绘界面暴露的无障碍节点）统一成
``IUIAutomationElement``，因此可以枚举到「按钮 / 编辑框 / 链接 / 列表项 /
文档文本」等元素及其名称与屏幕矩形。

实现方式：直接用 ``ctypes`` 调用 COM（``ole32!CoCreateInstance`` 创建
``CUIAutomation``，再按 vtable 索引调用方法），不引入 ``comtypes`` /
``uiautomation`` 等第三方包，保证最小环境可用。所有调用都在**当前线程**
完成（COM 单元线程模型），每次枚举创建并释放 COM 对象。

对外契约：

  - :func:`available` 探测当前环境能否使用 UIA；
  - :func:`enumerate_elements` 返回窗口内的控件列表（:class:`ElementInfo`），
    坐标为屏幕像素，与经典 Win32 枚举一致；失败时抛 :class:`UiaError`，
    调用方据此回退 ``EnumChildWindows``。

索引常量（vtable 序号）来自 Windows SDK 头文件（``uiautomationclient.h``）
的接口方法顺序，见 ``docs`` 注释；新增方法在此登记即可。
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import (
    POINTER,
    Structure,
    byref,
    c_int32,
    c_uint16,
    c_uint32,
    c_void_p,
    c_wchar_p,
)
from typing import Iterable

from . import winapi
from .elements import ElementInfo

logger = logging.getLogger(__name__)

# ── COM 常量 ────────────────────────────────────────────

COINIT_APARTMENTTHREADED = 0x2
CLSCTX_INPROC_SERVER = 0x1
S_OK = 0
S_FALSE = 1

#: UIA 树范围（``TreeScope``）：取全部后代（不含元素自身）
TREE_SCOPE_DESCENDANTS = 0x4


class GUID(Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


class UIA_RECT(Structure):
    _fields_ = [("left", c_int32), ("top", c_int32),
                ("right", c_int32), ("bottom", c_int32)]


def _guid(data1: int, data2: int, data3: int, rest: Iterable[int]) -> GUID:
    return GUID(data1, data2, data3, (ctypes.c_ubyte * 8)(*rest))


#: CLSID_CUIAutomation / IID_IUIAutomation（uiautomationclient.h）
CLSID_CUIAUTOMATION = _guid(
    0xFF48DBA4, 0x60EF, 0x4201,
    (0xAA, 0x87, 0x54, 0x10, 0x3E, 0xEF, 0x59, 0x4E),
)
IID_IUIAUTOMATION = _guid(
    0x30CBE57D, 0xD9D0, 0x452A,
    (0xAB, 0x13, 0x7A, 0xC5, 0xAC, 0x48, 0x25, 0xEE),
)

# ── vtable 索引（IUnknown 占 0/1/2，其后为接口方法） ─────

# IUIAutomation
_VT_UI_ELEMENT_FROM_HANDLE = 6
_VT_UI_CREATE_TRUE_CONDITION = 21

# IUIAutomationElement
_VT_EL_FIND_ALL = 6
_VT_EL_CURRENT_PROCESS_ID = 20
_VT_EL_CURRENT_CONTROL_TYPE = 21
_VT_EL_CURRENT_LOCALIZED_TYPE = 22
_VT_EL_CURRENT_NAME = 23
_VT_EL_CURRENT_IS_ENABLED = 28
_VT_EL_CURRENT_AUTOMATION_ID = 29
_VT_EL_CURRENT_CLASS_NAME = 30
_VT_EL_CURRENT_IS_CONTROL_ELEMENT = 33
_VT_EL_CURRENT_NATIVE_HANDLE = 36
_VT_EL_CURRENT_IS_OFFSCREEN = 38
_VT_EL_CURRENT_BOUNDING_RECTANGLE = 43

# IUIAutomationElementArray
_VT_ARR_LENGTH = 3
_VT_ARR_GET_ELEMENT = 4

# IUnknown 的 Release（用于释放所有 COM 接口指针）
_VT_RELEASE = 2

#: UIA 控件类型 ID → 统一控件类型名（``elements._TYPE_LABELS`` 提供中文标签）
_UIA_CONTROL_TYPES: dict[int, str] = {
    50000: "button", 50001: "calendar", 50002: "checkbox", 50003: "combobox",
    50004: "edit", 50005: "link", 50006: "image", 50007: "listitem",
    50008: "list", 50009: "menu", 50010: "menubar", 50011: "menuitem",
    50012: "progress", 50013: "radio", 50014: "scrollbar", 50015: "slider",
    50016: "spinner", 50017: "statusbar", 50018: "tab", 50019: "tabitem",
    50020: "text", 50021: "toolbar", 50022: "tooltip", 50023: "tree",
    50024: "treeitem", 50025: "custom", 50026: "group", 50027: "thumb",
    50028: "grid", 50029: "dataitem", 50030: "document", 50031: "splitbutton",
    50032: "window", 50033: "pane", 50034: "header", 50035: "headeritem",
    50036: "table", 50037: "titlebar", 50038: "separator",
}

#: 单次枚举的硬上限（防御元素爆炸式增长导致输出/耗时不可控）
MAX_ELEMENTS = 5000


class UiaError(RuntimeError):
    """UIA 不可用或调用失败（调用方据此回退经典 Win32 枚举）。"""


_LOCK = threading.Lock()
_AVAILABLE: bool | None = None


def _libraries():
    """懒加载并绑定 ole32 / oleaut32 的签名。"""
    ole32 = winapi.load_library("ole32.dll")
    ole32.CoInitializeEx.argtypes = [c_void_p, c_uint32]
    ole32.CoInitializeEx.restype = c_int32
    ole32.CoCreateInstance.argtypes = [
        POINTER(GUID), c_void_p, c_uint32, POINTER(GUID), POINTER(c_void_p),
    ]
    ole32.CoCreateInstance.restype = c_int32
    oleaut32 = winapi.load_library("oleaut32.dll")
    oleaut32.SysFreeString.argtypes = [c_void_p]
    oleaut32.SysFreeString.restype = None
    return ole32, oleaut32


def _method(ptr: int, index: int, restype, *argtypes):
    """按 vtable 索引取出 COM 方法并绑定签名。

    COM 接口指针指向的是一张函数指针表；``ptr`` 的第一个机器字是 ``lpVtbl``。
    """
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p)))[0]
    return winapi.CALLBACK(restype, c_void_p, *argtypes)(vtable[index])


def _release(ptr) -> None:
    """释放一个 COM 接口指针（失败忽略）。"""
    if not ptr:
        return
    try:
        _method(ptr, _VT_RELEASE, c_int32)(ptr)
    except Exception:  # pragma: no cover - 释放失败不应影响调用方
        logger.debug("释放 UIA 接口失败", exc_info=True)


def available() -> bool:
    """当前环境是否可用 UIA（Windows 平台且能创建 ``CUIAutomation``）。

    结果缓存（创建 COM 对象的成本只在首次探测时支付一次）。
    """
    global _AVAILABLE
    if not winapi.is_windows_platform():
        return False
    with _LOCK:
        if _AVAILABLE is None:
            _AVAILABLE = _probe()
        return _AVAILABLE


def _probe() -> bool:
    ole32, _oleaut32 = _libraries()
    automation = _create_automation(ole32)
    if not automation:
        return False
    _release(automation)
    return True


def _create_automation(ole32) -> int:
    """创建 ``CUIAutomation`` 实例，返回接口指针（失败返回 0）。"""
    try:
        ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    except OSError:  # pragma: no cover - 依赖系统调用
        return 0
    instance = c_void_p()
    hr = ole32.CoCreateInstance(
        byref(CLSID_CUIAUTOMATION), None, CLSCTX_INPROC_SERVER,
        byref(IID_IUIAUTOMATION), byref(instance),
    )
    if hr < 0 or not instance.value:
        logger.debug("创建 CUIAutomation 失败: hr=%s", hr)
        return 0
    return instance.value


def enumerate_elements(hwnd: int) -> list[ElementInfo]:
    """枚举 ``hwnd`` 窗口内（UIA 视图）的控件，返回 :class:`ElementInfo` 列表。

    坐标为屏幕像素，与经典 Win32 枚举一致；调用方（``win.py``）再按位置
    排序并换算窗口内坐标。

    Raises:
        UiaError: UIA 不可用、窗口句柄非法或 COM 调用失败。
        ValueError: ``hwnd`` 非法。
    """
    if not isinstance(hwnd, int) or isinstance(hwnd, bool) or hwnd <= 0:
        raise ValueError(f"窗口句柄非法，无法枚举 UIA 控件: {hwnd!r}")
    if not winapi.is_windows_platform():
        raise UiaError("当前平台不是 Windows，UIA 不可用")
    ole32, oleaut32 = _libraries()
    automation = _create_automation(ole32)
    if not automation:
        raise UiaError("创建 CUIAutomation 失败（UIA 不可用）")
    element = 0
    condition = 0
    array = 0
    try:
        element = _element_from_handle(automation, hwnd)
        if not element:
            raise UiaError(f"UIA 未能解析窗口 {hwnd}（ElementFromHandle 失败）")
        condition = _create_true_condition(automation)
        if not condition:
            raise UiaError("UIA 创建 TrueCondition 失败")
        array = _find_all(element, condition)
        if not array:
            return []
        return _collect(array, oleaut32)
    finally:
        _release(array)
        _release(condition)
        _release(element)
        _release(automation)


def _element_from_handle(automation: int, hwnd: int) -> int:
    out = c_void_p()
    hr = _method(automation, _VT_UI_ELEMENT_FROM_HANDLE, c_int32,
                 c_void_p, POINTER(c_void_p))(automation, c_void_p(hwnd), byref(out))
    if hr < 0 or not out.value:
        return 0
    return out.value


def _create_true_condition(automation: int) -> int:
    out = c_void_p()
    hr = _method(automation, _VT_UI_CREATE_TRUE_CONDITION, c_int32,
                 POINTER(c_void_p))(automation, byref(out))
    if hr < 0 or not out.value:
        return 0
    return out.value


def _find_all(element: int, condition: int) -> int:
    out = c_void_p()
    hr = _method(element, _VT_EL_FIND_ALL, c_int32,
                 c_int32, c_void_p, POINTER(c_void_p))(
        element, TREE_SCOPE_DESCENDANTS, condition, byref(out))
    if hr < 0 or not out.value:
        return 0
    return out.value


def _collect(array: int, oleaut32) -> list[ElementInfo]:
    count = c_int32()
    hr = _method(array, _VT_ARR_LENGTH, c_int32, POINTER(c_int32))(array, byref(count))
    if hr < 0 or count.value <= 0:
        return []
    total = min(int(count.value), MAX_ELEMENTS)
    elements: list[ElementInfo] = []
    for index in range(total):
        child = c_void_p()
        hr = _method(array, _VT_ARR_GET_ELEMENT, c_int32,
                     c_int32, POINTER(c_void_p))(array, index, byref(child))
        if hr < 0 or not child.value:
            continue
        try:
            info = _read_element(child.value, oleaut32)
        except Exception:  # pragma: no cover - 单个元素读取失败不应中断
            logger.debug("读取 UIA 元素失败 index=%s", index, exc_info=True)
            info = None
        finally:
            _release(child.value)
        if info is not None:
            elements.append(info)
    return elements


def _read_element(element: int, oleaut32) -> ElementInfo | None:
    """读取一个 UIA 元素为 :class:`ElementInfo`（非控件元素返回 None）。"""
    if not _read_bool(element, _VT_EL_CURRENT_IS_CONTROL_ELEMENT, default=True):
        return None
    rect = _read_rect(element)
    width = max(rect[2] - rect[0], 0)
    height = max(rect[3] - rect[1], 0)
    if width <= 0 or height <= 0:
        return None
    name = _read_bstr(element, _VT_EL_CURRENT_NAME, oleaut32)
    class_name = _read_bstr(element, _VT_EL_CURRENT_CLASS_NAME, oleaut32)
    automation_id = _read_bstr(element, _VT_EL_CURRENT_AUTOMATION_ID, oleaut32)
    control_type = _UIA_CONTROL_TYPES.get(_read_int(element, _VT_EL_CURRENT_CONTROL_TYPE))
    if control_type is None:
        control_type = ""
    offscreen = _read_bool(element, _VT_EL_CURRENT_IS_OFFSCREEN, default=False)
    enabled = _read_bool(element, _VT_EL_CURRENT_IS_ENABLED, default=True)
    handle = _read_int(element, _VT_EL_CURRENT_NATIVE_HANDLE, default=0)
    pid = _read_int(element, _VT_EL_CURRENT_PROCESS_ID, default=0)
    return ElementInfo(
        handle=int(handle or 0),
        pid=int(pid or 0),
        class_name=class_name,
        text=name,
        left=rect[0],
        top=rect[1],
        width=width,
        height=height,
        enabled=enabled,
        visible=not offscreen,
        depth=0,
        control_type_hint=control_type,
        source="uia",
        automation_id=automation_id,
    )


def _read_bstr(element: int, index: int, oleaut32) -> str:
    out = c_wchar_p()
    hr = _method(element, index, c_int32, POINTER(c_wchar_p))(element, byref(out))
    if hr < 0:
        return ""
    value = out.value or ""
    if out:
        try:
            oleaut32.SysFreeString(out)
        except Exception:  # pragma: no cover - 释放失败不影响结果
            pass
    return value


def _read_int(element: int, index: int, *, default: int = 0) -> int:
    out = c_int32()
    hr = _method(element, index, c_int32, POINTER(c_int32))(element, byref(out))
    return int(out.value) if hr >= 0 else default


def _read_bool(element: int, index: int, *, default: bool = False) -> bool:
    out = c_int32()
    hr = _method(element, index, c_int32, POINTER(c_int32))(element, byref(out))
    return bool(out.value) if hr >= 0 else default


def _read_rect(element: int) -> tuple[int, int, int, int]:
    out = UIA_RECT()
    hr = _method(element, _VT_EL_CURRENT_BOUNDING_RECTANGLE, c_int32,
                 POINTER(UIA_RECT))(element, byref(out))
    if hr < 0:
        return (0, 0, 0, 0)
    return (int(out.left), int(out.top), int(out.right), int(out.bottom))


__all__ = [
    "MAX_ELEMENTS",
    "UiaError",
    "available",
    "enumerate_elements",
]
