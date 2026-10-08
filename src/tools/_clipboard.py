"""系统剪贴板读写（``bash_opt`` 的 ``op=clipboard`` 与 ``type via='clipboard'`` 实现层）。

职责划分：

  - 本模块：平台后端注册表 + 公共入口 :func:`read_clipboard_text` /
    :func:`write_clipboard_text` / :func:`clear_clipboard`；
  - ``WindowsClipboardBackend`` / ``MacOSClipboardBackend`` /
    ``X11ClipboardBackend``：各平台实现，契约一致
    （``name`` + ``supports()`` + ``read_text()`` + ``write_text(text)``）。

为什么需要剪贴板：向 GUI 应用输入**长文本 / 特殊字符（中文、emoji、多行）**
时，逐字符合成按键既慢又容易被目标程序丢字或误解释快捷键；把文本放入系统
剪贴板再发送粘贴键（Ctrl+V / Cmd+V）是最可靠的方式，游戏、Electron、
浏览器、远程桌面都接受。

换行约定：Windows 剪贴板文本规范是 CRLF，读写时做归一化
（写入把 ``\\n`` 变为 ``\\r\\n``，读取把 ``\\r\\n`` 还原为 ``\\n``），
粘贴到记事本 / 编辑框时不会因为缺回车而粘成一行。

扩展方式：新增平台只需实现上述契约并 :func:`register_backend`（内置后端按
Windows → macOS → X11 顺序探测），无需改动工具层。
"""

from __future__ import annotations

import ctypes
import logging
import shutil
import subprocess
import sys
import threading
import time
from functools import lru_cache
from typing import Callable

logger = logging.getLogger(__name__)

#: 外部命令（xclip / xsel / pbcopy / pbpaste）超时（秒）
_COMMAND_TIMEOUT = 10.0

#: Windows 剪贴板打开的尝试次数与间隔（剪贴板被别的进程占用时短暂重试）
_OPEN_ATTEMPTS = 5
_OPEN_RETRY_INTERVAL = 0.05

#: Windows 剪贴板文本格式
CF_UNICODETEXT = 13

#: GlobalAlloc 标志：可移动内存（剪贴板要求）
GMEM_MOVEABLE = 0x0002


class ClipboardError(RuntimeError):
    """剪贴板读写失败（平台工具缺失 / 剪贴板被占用 / 系统调用失败）。

    消息面向大模型，需自带可执行的下一步提示。
    """


_BACKENDS: list = []
_LOCK = threading.RLock()
_BUILTINS_LOADED = False


def register_backend(backend, *, prepend: bool = False) -> Callable[[], None]:
    """注册剪贴板后端，返回幂等撤销函数。"""
    with _LOCK:
        if prepend:
            _BACKENDS.insert(0, backend)
        else:
            _BACKENDS.append(backend)

        def _undo() -> None:
            with _LOCK:
                if backend in _BACKENDS:
                    _BACKENDS.remove(backend)

        return _undo


def available_backends() -> list:
    """当前已注册的后端（含不支持当前平台的，按探测顺序）。"""
    _ensure_builtins()
    with _LOCK:
        return list(_BACKENDS)


def resolve_backend():
    """返回当前平台上第一个可用的剪贴板后端（无则 None）。"""
    for backend in available_backends():
        try:
            if backend.supports():
                return backend
        except Exception:  # pragma: no cover - 后端探测异常不应中断
            logger.debug("剪贴板后端 %s 探测失败", getattr(backend, "name", backend),
                         exc_info=True)
    return None


def read_clipboard_text() -> str:
    """读取系统剪贴板文本（无文本内容时返回空串）。

    Raises:
        ClipboardError: 当前平台没有可用后端，或读取失败。
    """
    return _require_backend().read_text()


def write_clipboard_text(text: str) -> None:
    """把文本写入系统剪贴板（覆盖原内容）。

    Raises:
        ClipboardError: 当前平台没有可用后端，或写入失败。
    """
    _require_backend().write_text(str(text))


def clear_clipboard() -> None:
    """清空系统剪贴板文本内容（等价于写入空串）。"""
    write_clipboard_text("")


def _require_backend():
    backend = resolve_backend()
    if backend is None:
        raise ClipboardError(
            f"当前平台（{sys.platform}）没有可用的剪贴板后端"
            f"（Linux 需安装 xclip 或 xsel；macOS 需要 pbcopy/pbpaste）"
        )
    return backend


def normalize_newlines(text: str) -> str:
    """把任意换行统一为 ``\\n``（读取剪贴板后归一化）。"""
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def to_platform_newlines(text: str) -> str:
    """把 ``\\n`` 归一化为平台剪贴板的换行（Windows 用 CRLF）。"""
    return normalize_newlines(text).replace("\n", "\r\n")


class WindowsClipboardBackend:
    """Windows 剪贴板后端（原生 Win32 API，无第三方依赖）。"""

    name = "windows"

    def supports(self) -> bool:
        from ._screenshot import winapi
        return winapi.is_windows_platform()

    # ── 库与签名（懒加载 + 缓存） ────────────────────────

    @staticmethod
    @lru_cache(maxsize=1)
    def _libs():
        """返回并绑定 ``(user32, kernel32)``（首次调用时设置函数签名）。"""
        load = ctypes.WinDLL if hasattr(ctypes, "WinDLL") else ctypes.CDLL
        user = load("user32.dll")
        kernel = load("kernel32.dll")
        user.OpenClipboard.argtypes = [ctypes.c_void_p]
        user.OpenClipboard.restype = ctypes.c_int
        user.CloseClipboard.argtypes = []
        user.CloseClipboard.restype = ctypes.c_int
        user.EmptyClipboard.argtypes = []
        user.EmptyClipboard.restype = ctypes.c_int
        user.GetClipboardData.argtypes = [ctypes.c_uint32]
        user.GetClipboardData.restype = ctypes.c_void_p
        user.SetClipboardData.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        user.SetClipboardData.restype = ctypes.c_void_p
        kernel.GlobalAlloc.argtypes = [ctypes.c_uint32, ctypes.c_size_t]
        kernel.GlobalAlloc.restype = ctypes.c_void_p
        kernel.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel.GlobalLock.restype = ctypes.c_void_p
        kernel.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel.GlobalUnlock.restype = ctypes.c_int
        kernel.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel.GlobalFree.restype = ctypes.c_void_p
        return user, kernel

    def _read(self) -> str:
        user, kernel = self._libs()
        self._open(user)
        try:
            handle = user.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = kernel.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel.GlobalUnlock(handle)
        finally:
            user.CloseClipboard()

    def _write(self, text: str) -> None:
        payload = to_platform_newlines(text)
        user, kernel = self._libs()
        size = (len(payload) + 1) * ctypes.sizeof(ctypes.c_wchar)
        handle = kernel.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            raise ClipboardError("剪贴板写入失败：GlobalAlloc 无法分配内存")
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            kernel.GlobalFree(handle)
            raise ClipboardError("剪贴板写入失败：GlobalLock 无法锁定内存")
        try:
            ctypes.memmove(pointer, ctypes.create_unicode_buffer(payload),
                           size)
        finally:
            kernel.GlobalUnlock(handle)
        self._open(user)
        try:
            if not user.EmptyClipboard():
                kernel.GlobalFree(handle)
                raise ClipboardError("剪贴板写入失败：EmptyClipboard 调用失败")
            if not user.SetClipboardData(CF_UNICODETEXT, handle):
                kernel.GlobalFree(handle)
                raise ClipboardError("剪贴板写入失败：SetClipboardData 调用失败")
            # 成功后内存所有权归系统，不能再 GlobalFree
        finally:
            user.CloseClipboard()

    @staticmethod
    def _open(user) -> None:
        """打开剪贴板（被其它进程占用时短暂重试）。"""
        last_error = None
        for attempt in range(_OPEN_ATTEMPTS):
            try:
                if user.OpenClipboard(None):
                    return
            except OSError as exc:  # pragma: no cover - 依赖系统调用
                last_error = exc
            if attempt + 1 < _OPEN_ATTEMPTS:
                time.sleep(_OPEN_RETRY_INTERVAL)
        raise ClipboardError(
            f"无法打开系统剪贴板（被其它进程占用或调用失败）: {last_error or 'OpenClipboard 返回 0'}"
        )

    def read_text(self) -> str:
        try:
            return normalize_newlines(self._read())
        except ClipboardError:
            raise
        except OSError as exc:  # pragma: no cover - 依赖系统调用
            raise ClipboardError(f"读取剪贴板失败: {exc}") from exc

    def write_text(self, text: str) -> None:
        try:
            self._write(text)
        except ClipboardError:
            raise
        except OSError as exc:  # pragma: no cover - 依赖系统调用
            raise ClipboardError(f"写入剪贴板失败: {exc}") from exc


class _CommandClipboardBackend:
    """基于外部命令的剪贴板后端基类（读取命令 / 写入命令由子类给出）。"""

    name = "command"
    #: 读取命令（标准输出即剪贴板文本）
    read_command: tuple[str, ...] = ()
    #: 写入命令（标准输入接收文本）
    write_command: tuple[str, ...] = ()

    def supports(self) -> bool:
        return bool(self.read_command and self.write_command
                    and shutil.which(self.read_command[0])
                    and shutil.which(self.write_command[0]))

    def read_text(self) -> str:
        return normalize_newlines(self._run(list(self.read_command), None))

    def write_text(self, text: str) -> None:
        self._run(list(self.write_command), str(text))

    def _run(self, command: list[str], payload: str | None) -> str:
        try:
            completed = subprocess.run(
                command, input=payload, capture_output=True, text=True,
                timeout=_COMMAND_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ClipboardError(
                f"剪贴板命令执行失败（{command[0]}）: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()
            summary = detail[-1] if detail else f"退出码 {completed.returncode}"
            raise ClipboardError(f"剪贴板命令失败（{command[0]}）: {summary}")
        return completed.stdout


class MacOSClipboardBackend(_CommandClipboardBackend):
    """macOS 剪贴板后端（pbcopy / pbpaste）。"""

    name = "macos"
    read_command = ("pbpaste",)
    write_command = ("pbcopy",)

    def supports(self) -> bool:
        return sys.platform == "darwin" and super().supports()


class X11ClipboardBackend(_CommandClipboardBackend):
    """X11 剪贴板后端（xclip 优先，xsel 回退）。"""

    name = "x11"

    def supports(self) -> bool:
        if sys.platform.startswith(("win", "cygwin", "msys")) or sys.platform == "darwin":
            return False
        return bool(_x11_commands())

    def _commands(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return _x11_commands() or ((), ())

    def read_text(self) -> str:
        read, _write = self._commands()
        return normalize_newlines(self._run(list(read), None))

    def write_text(self, text: str) -> None:
        _read, write = self._commands()
        self._run(list(write), str(text))


def _x11_commands() -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """返回可用的 ``(读取命令, 写入命令)``（xclip 优先，xsel 回退）。"""
    if shutil.which("xclip"):
        return (("xclip", "-selection", "clipboard", "-o"),
                ("xclip", "-selection", "clipboard", "-i"))
    if shutil.which("xsel"):
        return (("xsel", "--clipboard", "--output"),
                ("xsel", "--clipboard", "--input"))
    return None


def _ensure_builtins() -> None:
    """幂等注册内置后端（延迟导入，避免包初始化期的循环引用）。"""
    global _BUILTINS_LOADED
    with _LOCK:
        if _BUILTINS_LOADED:
            return
        _BUILTINS_LOADED = True
    for backend in (WindowsClipboardBackend(), MacOSClipboardBackend(),
                    X11ClipboardBackend()):
        register_backend(backend)


__all__ = [
    "CF_UNICODETEXT",
    "ClipboardError",
    "MacOSClipboardBackend",
    "WindowsClipboardBackend",
    "X11ClipboardBackend",
    "available_backends",
    "clear_clipboard",
    "normalize_newlines",
    "read_clipboard_text",
    "register_backend",
    "resolve_backend",
    "to_platform_newlines",
    "write_clipboard_text",
]
