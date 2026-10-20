"""原生 Windows 控制台输入后端（cmd / conhost / Windows Terminal）。

Python 的 ``select`` 在 Windows 上只支持 socket，控制台 stdin 会抛
``OSError(10038/10093)``——TUI 既有的 select+os.read 输入路径在原生
Windows 控制台不可用（Cygwin/MSYS 的 Python 走伪终端，不受影响）。
本模块用 ``ReadConsoleInputW`` 读取按键事件，翻译为 TUI 解析器使用的
ANSI/UTF-8 字节流，使 ``python chat.py`` 在 cmd 中可交互。

仅在 native Windows 且 stdin 为控制台句柄时启用；其余平台走既有
select 路径（``supported()`` 返回 False）。

raw 模式：``enable_raw()`` 关闭 ENABLE_LINE_INPUT / ENABLE_ECHO_INPUT /
ENABLE_PROCESSED_INPUT / ENABLE_QUICK_EDIT_MODE，并打开
ENABLE_WINDOW_INPUT——不关闭 PROCESSED_INPUT 时 Ctrl+C 会被系统吞成
CTRL_C_EVENT，无法交给 TUI 的中断逻辑。终端模式随
``RawModeController`` 启停，退出时完整还原。
"""

from __future__ import annotations

import logging
import sys
from ctypes import POINTER, Structure, Union, byref, c_void_p, c_wchar, wintypes

_logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

# ── 控制台模式标志 ────────────────────────────────────────
_ENABLE_PROCESSED_INPUT = 0x0001
_ENABLE_LINE_INPUT = 0x0002
_ENABLE_ECHO_INPUT = 0x0004
_ENABLE_WINDOW_INPUT = 0x0008
_ENABLE_QUICK_EDIT_MODE = 0x0040
_ENABLE_EXTENDED_FLAGS = 0x0080

# ── 虚拟键码 ──────────────────────────────────────────────
_VK_BACK = 0x08
_VK_TAB = 0x09
_VK_RETURN = 0x0D
_VK_ESCAPE = 0x1B
_VK_SPACE = 0x20
_VK_PRIOR = 0x21
_VK_NEXT = 0x22
_VK_END = 0x23
_VK_HOME = 0x24
_VK_LEFT = 0x25
_VK_UP = 0x26
_VK_RIGHT = 0x27
_VK_DOWN = 0x28
_VK_INSERT = 0x2D
_VK_DELETE = 0x2E
_VK_F1 = 0x70
_VK_F12 = 0x7B

# ── 控制键状态位 ──────────────────────────────────────────
_RIGHT_ALT_PRESSED = 0x0001
_LEFT_ALT_PRESSED = 0x0002
_RIGHT_CTRL_PRESSED = 0x0004
_LEFT_CTRL_PRESSED = 0x0008
_SHIFT_PRESSED = 0x0010

#: 方向键字母（xterm CSI A/B/C/D）
_ARROW_KEYS = {_VK_UP: "A", _VK_DOWN: "B", _VK_RIGHT: "C", _VK_LEFT: "D"}
#: Home/End（xterm CSI H/F）
_HOME_END_KEYS = {_VK_HOME: "H", _VK_END: "F"}
#: Insert/Delete/PgUp/PgDn（xterm CSI n~）
_TILDE_KEYS = {
    _VK_INSERT: "2",
    _VK_DELETE: "3",
    _VK_PRIOR: "5",
    _VK_NEXT: "6",
}
#: F1-F4（SS3 P/Q/R/S）与 F5-F12（CSI n~）
_FKEYS = {
    _VK_F1: ("ss3", "P"),
    0x71: ("ss3", "Q"),
    0x72: ("ss3", "R"),
    0x73: ("ss3", "S"),
    0x74: ("tilde", "15"),
    0x75: ("tilde", "17"),
    0x76: ("tilde", "18"),
    0x77: ("tilde", "19"),
    0x78: ("tilde", "20"),
    0x79: ("tilde", "21"),
    0x7A: ("tilde", "23"),
    _VK_F12: ("tilde", "24"),
}

# ── Win32 结构 ────────────────────────────────────────────
if _IS_WINDOWS:
    from ctypes import WinDLL

    try:
        _kernel32 = WinDLL("kernel32", use_last_error=True)
    except Exception:  # pragma: no cover - 防御（kernel32 加载失败）
        _logger.debug("kernel32 加载失败，原生 Windows 控制台后端不可用", exc_info=True)
        _kernel32 = None
else:  # pragma: no cover - 非 Windows 不加载
    _kernel32 = None

_STD_INPUT_HANDLE = -10


class _COORD(Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class _KEY_EVENT_RECORD(Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("UnicodeChar", c_wchar),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class _MOUSE_EVENT_RECORD(Structure):
    _fields_ = [
        ("dwMousePosition", _COORD),
        ("dwButtonState", wintypes.DWORD),
        ("dwControlKeyState", wintypes.DWORD),
        ("dwEventFlags", wintypes.DWORD),
    ]


class _WINDOW_BUFFER_SIZE_RECORD(Structure):
    _fields_ = [("dwSize", _COORD)]


class _EVENT_UNION(Union):
    _fields_ = [
        ("KeyEvent", _KEY_EVENT_RECORD),
        ("MouseEvent", _MOUSE_EVENT_RECORD),
        ("WindowBufferSizeEvent", _WINDOW_BUFFER_SIZE_RECORD),
    ]


class _INPUT_RECORD(Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _EVENT_UNION)]


_KEY_EVENT = 0x0001

if _IS_WINDOWS and _kernel32 is not None:
    # 显式声明签名：GetStdHandle 返回 64 位句柄，默认 c_int 会截断。
    _kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    _kernel32.GetStdHandle.restype = c_void_p
    _kernel32.GetConsoleMode.argtypes = [c_void_p, POINTER(wintypes.DWORD)]
    _kernel32.GetConsoleMode.restype = wintypes.BOOL
    _kernel32.SetConsoleMode.argtypes = [c_void_p, wintypes.DWORD]
    _kernel32.SetConsoleMode.restype = wintypes.BOOL
    _kernel32.GetNumberOfConsoleInputEvents.argtypes = [c_void_p, POINTER(wintypes.DWORD)]
    _kernel32.GetNumberOfConsoleInputEvents.restype = wintypes.BOOL
    _kernel32.ReadConsoleInputW.argtypes = [
        c_void_p, POINTER(_INPUT_RECORD), wintypes.DWORD, POINTER(wintypes.DWORD),
    ]
    _kernel32.ReadConsoleInputW.restype = wintypes.BOOL

#: 输入句柄（模块级缓存：GetStdHandle 结果在进程生命周期内稳定）
_stdin_handle: c_void_p | None = None
_handle_ready = False
#: supported() 判定缓存（同一进程内控制台属性不变）
_supported_cache: bool | None = None
#: raw 模式保存的原始控制台模式（None = 未进入 raw）
_saved_console_mode: int | None = None
#: 跨事件的高代理项（UTF-16 代理对分两条 KEY_EVENT 到达）
_pending_high_surrogate: int | None = None

#: 单帧最多读取的输入事件数（大段粘贴分帧消费，避免单帧阻塞）
_MAX_EVENTS_PER_READ = 256


def _get_stdin_handle():
    """取 stdin 控制台句柄；失败/非控制台返回 None。"""
    global _stdin_handle, _handle_ready
    if _handle_ready:
        return _stdin_handle
    _handle_ready = True
    if not _IS_WINDOWS or _kernel32 is None:  # pragma: no cover - 平台分支
        return None
    try:
        handle = _kernel32.GetStdHandle(_STD_INPUT_HANDLE)
    except Exception:  # pragma: no cover - 防御
        _logger.debug("GetStdHandle 失败", exc_info=True)
        return None
    if handle in (None, 0, c_void_p(-1).value):
        return None
    _stdin_handle = c_void_p(handle)
    return _stdin_handle


def supported() -> bool:
    """stdin 是否为可读的原生 Windows 控制台。"""
    global _supported_cache
    if _supported_cache is not None:
        return _supported_cache
    handle = _get_stdin_handle()
    ok = False
    if handle is not None:
        try:
            mode = wintypes.DWORD()
            ok = bool(_kernel32.GetConsoleMode(handle, byref(mode)))
        except Exception:  # pragma: no cover - 防御
            _logger.debug("GetConsoleMode 失败", exc_info=True)
    _supported_cache = ok
    return ok


def enable_raw() -> bool:
    """进入控制台 raw 模式（幂等）；成功返回 True。"""
    global _saved_console_mode
    if _saved_console_mode is not None:
        return True
    handle = _get_stdin_handle()
    if handle is None:  # pragma: no cover - 调用方先判 supported
        return False
    try:
        mode = wintypes.DWORD()
        if not _kernel32.GetConsoleMode(handle, byref(mode)):
            return False
        saved = int(mode.value)
        new_mode = (
            (saved & ~(_ENABLE_LINE_INPUT | _ENABLE_ECHO_INPUT | _ENABLE_PROCESSED_INPUT
                       | _ENABLE_QUICK_EDIT_MODE))
            | _ENABLE_EXTENDED_FLAGS
            | _ENABLE_WINDOW_INPUT
        )
        if not _kernel32.SetConsoleMode(handle, new_mode):
            return False
        _saved_console_mode = saved
        return True
    except Exception:  # pragma: no cover - 防御
        _logger.debug("进入 Windows 控制台 raw 模式失败", exc_info=True)
        return False


def disable_raw() -> bool:
    """还原控制台模式（幂等）；成功/未进入返回 True。"""
    global _saved_console_mode, _pending_high_surrogate
    if _saved_console_mode is None:
        return True
    handle = _get_stdin_handle()
    saved, _saved_console_mode = _saved_console_mode, None
    _pending_high_surrogate = None
    if handle is None:  # pragma: no cover - 防御
        return False
    try:
        return bool(_kernel32.SetConsoleMode(handle, saved))
    except Exception:  # pragma: no cover - 防御
        _logger.debug("还原 Windows 控制台模式失败", exc_info=True)
        return False


def _modifier_code(ctrl: bool, alt: bool, shift: bool) -> int:
    """xterm 修饰键参数（1 + Shift + 2*Alt + 4*Ctrl）。"""
    return 1 + (1 if shift else 0) + (2 if alt else 0) + (4 if ctrl else 0)


def translate_key_event(
    virtual_key: int,
    unicode_char: str,
    control_state: int,
    repeat: int = 1,
) -> bytes:
    """把单个 Windows KEY_EVENT 翻译为 TUI 解析器使用的字节序列。

    纯函数（不触达 Win32），供单元测试直接调用。

    Args:
        virtual_key: 虚拟键码（VK_*）。
        unicode_char: 事件携带的字符（可能为空）。
        control_state: 修饰键状态位（Shift/Ctrl/Alt）。
        repeat: 重复次数（按键长按）。

    Returns:
        对应 ANSI/UTF-8 字节；无输出返回 ``b""``。
    """
    ctrl = bool(control_state & (_LEFT_CTRL_PRESSED | _RIGHT_CTRL_PRESSED))
    alt = bool(control_state & (_LEFT_ALT_PRESSED | _RIGHT_ALT_PRESSED))
    shift = bool(control_state & _SHIFT_PRESSED)
    mod = _modifier_code(ctrl, alt, shift)
    repeat = max(1, min(int(repeat or 1), 64))

    # 键名键优先（Backspace 在 Windows 控制台报告 0x08，须映射为 0x7f，
    # 否则会被 TUI 当作 Ctrl+H 打开轨迹视图）。
    if virtual_key == _VK_BACK:
        chunk = b"\x7f"
    elif virtual_key == _VK_TAB:
        chunk = b"\x1b[Z" if shift else b"\t"
    elif virtual_key == _VK_RETURN:
        chunk = b"\r"
    elif virtual_key == _VK_ESCAPE:
        chunk = b"\x1b"
    elif virtual_key in _ARROW_KEYS:
        letter = _ARROW_KEYS[virtual_key]
        chunk = f"\x1b[{letter}".encode() if mod == 1 else f"\x1b[1;{mod}{letter}".encode()
    elif virtual_key in _HOME_END_KEYS:
        letter = _HOME_END_KEYS[virtual_key]
        chunk = f"\x1b[{letter}".encode() if mod == 1 else f"\x1b[1;{mod}{letter}".encode()
    elif virtual_key in _TILDE_KEYS:
        code = _TILDE_KEYS[virtual_key]
        chunk = f"\x1b[{code}~".encode() if mod == 1 else f"\x1b[{code};{mod}~".encode()
    elif virtual_key in _FKEYS:
        kind, code = _FKEYS[virtual_key]
        if kind == "ss3":
            chunk = f"\x1bO{code}".encode() if mod == 1 else f"\x1b[1;{mod}{code}".encode()
        else:
            chunk = f"\x1b[{code}~".encode() if mod == 1 else f"\x1b[{code};{mod}~".encode()
    elif ctrl and not alt:
        # Ctrl+字母 → C0 控制字节（Ctrl+H=0x08、Ctrl+C=0x03 …）
        chunk = b""
        if 0x41 <= virtual_key <= 0x5A:
            chunk = bytes([virtual_key - 0x40])
        elif virtual_key == _VK_SPACE:
            chunk = b"\x00"
        elif virtual_key == 0xDB:  # [
            chunk = b"\x1b"
        elif virtual_key == 0xDC:  # \
            chunk = b"\x1c"
        elif virtual_key == 0xDD:  # ]
            chunk = b"\x1d"
        elif virtual_key == 0xBF:  # /
            chunk = b"\x1f"
        if not chunk and unicode_char:
            chunk = _encode_char(unicode_char)
    elif unicode_char:
        chunk = _encode_char(unicode_char)
        if alt and not ctrl:
            # Alt+字符：xterm 风格 ESC 前缀（解析器识别为 alt_char）
            chunk = b"\x1b" + chunk
    else:
        chunk = b""

    return chunk * repeat


def _encode_char(char: str) -> bytes:
    """UTF-8 编码单个字符；孤立代理项返回空（由 read_bytes 拼接）。"""
    try:
        return char.encode("utf-8")
    except UnicodeEncodeError:  # pragma: no cover - 代理项由 read_bytes 处理
        return b""


def read_bytes() -> bytes:
    """读取当前可用的控制台输入并翻译为字节流。

    Returns:
        翻译后的字节（可能为空——无输入或仅按键抬起/焦点事件）。
    """
    global _pending_high_surrogate
    handle = _get_stdin_handle()
    if handle is None:  # pragma: no cover - 调用方先判 supported
        return b""
    try:
        available = wintypes.DWORD()
        if not _kernel32.GetNumberOfConsoleInputEvents(handle, byref(available)):
            return b""
        count = min(int(available.value), _MAX_EVENTS_PER_READ)
        if count <= 0:
            return b""
        records = (_INPUT_RECORD * count)()
        read = wintypes.DWORD()
        if not _kernel32.ReadConsoleInputW(handle, records, count, byref(read)):
            return b""
    except Exception:  # pragma: no cover - 防御
        _logger.debug("ReadConsoleInputW 失败", exc_info=True)
        return b""

    out = bytearray()
    for i in range(int(read.value)):
        record = records[i]
        if record.EventType != _KEY_EVENT:
            continue  # 鼠标/尺寸/焦点事件：尺寸有轮询兜底，鼠标暂不支持
        key = record.Event.KeyEvent
        if not key.bKeyDown:
            continue
        char = key.UnicodeChar or ""
        code = ord(char) if len(char) == 1 else 0
        if 0xD800 <= code <= 0xDBFF:
            # UTF-16 高代理项：等待下一条低代理项事件拼接
            _pending_high_surrogate = code
            continue
        if 0xDC00 <= code <= 0xDFFF and _pending_high_surrogate is not None:
            high, _pending_high_surrogate = _pending_high_surrogate, None
            char = chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00))
        elif _pending_high_surrogate is not None:
            _pending_high_surrogate = None
        out += translate_key_event(
            int(key.wVirtualKeyCode), char, int(key.dwControlKeyState),
            int(key.wRepeatCount),
        )
    return bytes(out)


__all__ = [
    "supported",
    "enable_raw",
    "disable_raw",
    "read_bytes",
    "translate_key_event",
]
