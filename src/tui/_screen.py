"""纯 ANSI 终端屏幕管理 — 零第三方依赖。

提供终端尺寸查询、字符宽度计算、ANSI 转义序列生成、
SIGWINCH 信号处理等基础设施。所有函数为纯字符串返回或
直接写入 ``sys.__stdout__``，不依赖 blessed/wcwidth 等第三方库。

设计模式: 外观（Facade）— 作为所有终端 I/O 的统一入口。
"""

from __future__ import annotations

import fcntl
import io
import os
import signal
import struct
import sys
import termios
from typing import Callable


# ═══════════════════════════════════════════════════════════
# 终端尺寸查询
# ═══════════════════════════════════════════════════════════

def _get_terminal_size() -> tuple[int, int]:
    """获取终端尺寸 (宽度, 高度)。

    优先使用 ``fcntl.ioctl(TIOCGWINSZ)`` 获取精确尺寸，
    fallback ``os.get_terminal_size()``，
    最终兜底 (80, 24)。

    Returns:
        (cols, rows) 终端宽度和高度。
    """
    for fd_src in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = fd_src.fileno()
        except (io.UnsupportedOperation, OSError, AttributeError):
            continue
        try:
            # TIOCGWINSZ 结构体: unsigned short ws_row, ws_col, ws_xpixel, ws_ypixel
            buf = fcntl.ioctl(fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
            rows, cols, _, _ = struct.unpack("HHHH", buf)
            if rows > 0 and cols > 0:
                return (cols, rows)
        except (OSError, struct.error):
            continue

    # Fallback: os.get_terminal_size()
    try:
        ts = os.get_terminal_size()
        if ts.columns > 0 and ts.lines > 0:
            return (ts.columns, ts.lines)
    except (OSError, ValueError):
        pass

    # 最终兜底
    return (80, 24)


# ═══════════════════════════════════════════════════════════
# 字符宽度计算
# ═══════════════════════════════════════════════════════════

# CJK Unified Ideographs 及扩展区范围
_CJK_RANGES: list[tuple[int, int]] = [
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Unified Ideographs Extension A
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
    (0x2F800, 0x2FA1F),  # CJK Compatibility Ideographs Supplement
    (0x20000, 0x2CEAF),  # CJK Unified Ideographs Extension B-F
    (0x30000, 0x3134F),  # CJK Unified Ideographs Extension G-H
]

# 组合标记/零宽字符范围
_ZERO_WIDTH_RANGES: list[tuple[int, int]] = [
    (0x0300, 0x036F),   # Combining Diacritical Marks
    (0x1AB0, 0x1AFF),   # Combining Diacritical Marks Extended
    (0x1DC0, 0x1DFF),   # Combining Diacritical Marks Supplement
    (0x20D0, 0x20FF),   # Combining Diacritical Marks for Symbols
    (0xFE00, 0xFE0F),   # Variation Selectors
    (0xFE20, 0xFE2F),   # Combining Half Marks
    (0xE0100, 0xE01EF), # Variation Selectors Supplement
]

# 全角字符范围（宽度为2的非CJK字符）
_FULLWIDTH_RANGES: list[tuple[int, int]] = [
    (0xFF01, 0xFF60),   # Fullwidth Forms
    (0xFFE0, 0xFFE6),   # Fullwidth Signs
]


def _in_ranges(cp: int, ranges: list[tuple[int, int]]) -> bool:
    """检查码点是否在范围内。"""
    return any(lo <= cp <= hi for lo, hi in ranges)


def wcswidth_simple(text: str) -> int:
    """计算字符串的显示宽度（零第三方依赖）。

    规则：
    - ASCII 可打印字符 (0x20-0x7E)：宽度 1
    - 控制字符 (0x00-0x1F, 0x7F-0x9F)：宽度 0
    - CJK 字符：宽度 2
    - 全角字符：宽度 2
    - 组合标记/零宽字符：宽度 0
    - 其他：宽度 1

    Args:
        text: 输入字符串。

    Returns:
        显示宽度（整数）。
    """
    width = 0
    for ch in text:
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            width += 1
        elif cp < 0x20 or (0x7F <= cp <= 0x9F):
            width += 0  # 控制字符
        elif _in_ranges(cp, _CJK_RANGES):
            width += 2
        elif _in_ranges(cp, _FULLWIDTH_RANGES):
            width += 2
        elif _in_ranges(cp, _ZERO_WIDTH_RANGES):
            width += 0
        else:
            width += 1
    return width


# ═══════════════════════════════════════════════════════════
# 滚动区域 (DECSTBM)
# ═══════════════════════════════════════════════════════════

def set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域。

    Args:
        top: 顶部行号（1-based）。
        bottom: 底部行号（1-based）。

    Returns:
        ANSI DECSTBM 序列。
    """
    return f"\033[{top};{bottom}r"


def reset_scroll_region() -> str:
    """重置滚动区域为全屏。

    Returns:
        ANSI 序列。
    """
    return "\033[r"


# ═══════════════════════════════════════════════════════════
# 光标控制
# ═══════════════════════════════════════════════════════════

def cursor_save() -> str:
    """保存光标位置 (SCOSC)。

    Returns:
        ANSI SCOSC 序列。
    """
    return "\033[s"


def cursor_restore() -> str:
    """恢复光标位置 (SCRC)。

    Returns:
        ANSI SCRC 序列。
    """
    return "\033[u"


def cursor_goto(row: int, col: int) -> str:
    """移动光标到指定位置 (CUP, 1-based)。

    Args:
        row: 目标行号（1-based）。
        col: 目标列号（1-based）。

    Returns:
        ANSI CUP 序列。
    """
    return f"\033[{row};{col}H"


def cursor_up(n: int = 1) -> str:
    """光标上移 n 行 (CUU)。

    Args:
        n: 移动行数。

    Returns:
        ANSI CUU 序列。
    """
    return f"\033[{n}A"


def cursor_down(n: int = 1) -> str:
    """光标下移 n 行 (CUD)。

    Args:
        n: 移动行数。

    Returns:
        ANSI CUD 序列。
    """
    return f"\033[{n}B"


def cursor_forward(n: int = 1) -> str:
    """光标右移 n 列 (CUF)。

    Args:
        n: 移动列数。

    Returns:
        ANSI CUF 序列。
    """
    return f"\033[{n}C"


def cursor_back(n: int = 1) -> str:
    """光标左移 n 列 (CUB)。

    Args:
        n: 移动列数。

    Returns:
        ANSI CUB 序列。
    """
    return f"\033[{n}D"


def cursor_hide() -> str:
    """隐藏光标。

    Returns:
        ANSI DECTCEM 序列（隐藏）。
    """
    return "\033[?25l"


def cursor_show() -> str:
    """显示光标。

    Returns:
        ANSI DECTCEM 序列（显示）。
    """
    return "\033[?25h"


# ═══════════════════════════════════════════════════════════
# 清屏/清行
# ═══════════════════════════════════════════════════════════

def clear_line() -> str:
    """清除从光标到行尾的内容 (EL 0)。

    Returns:
        ANSI EL 序列。
    """
    return "\r\033[K"


def clear_line_full() -> str:
    """清除整行 (EL 2)。

    Returns:
        ANSI EL 序列。
    """
    return "\r\033[2K"


def clear_screen_from_cursor() -> str:
    """清除从光标到屏幕末尾 (ED 0)。

    Returns:
        ANSI ED 序列。
    """
    return "\033[0J"


def clear_screen_to_cursor() -> str:
    """清除从屏幕开头到光标 (ED 1)。

    Returns:
        ANSI ED 序列。
    """
    return "\033[1J"


def clear_screen() -> str:
    """清除整个屏幕 (ED 2) 并归位光标。

    Returns:
        ANSI ED + CUP 序列。
    """
    return "\033[2J\033[H"


def move_clear(row: int) -> str:
    """组合光标定位 + 清行。

    Args:
        row: 目标行号（1-based）。

    Returns:
        CUP + EL 组合序列。
    """
    return f"\033[{row};1H\033[K"


# ═══════════════════════════════════════════════════════════
# 滚动
# ═══════════════════════════════════════════════════════════

def scroll_up(n: int = 1) -> str:
    """向上滚动 n 行 (SU)。

    Args:
        n: 滚动行数。

    Returns:
        ANSI SU 序列。
    """
    return f"\033[{n}S"


def scroll_down(n: int = 1) -> str:
    """向下滚动 n 行 (SD)。

    Args:
        n: 滚动行数。

    Returns:
        ANSI SD 序列。
    """
    return f"\033[{n}T"


# ═══════════════════════════════════════════════════════════
# 颜色 / SGR
# ═══════════════════════════════════════════════════════════

def sgr(*codes: int) -> str:
    """构建 SGR 序列。

    Args:
        codes: SGR 参数码。

    Returns:
        ANSI SGR 序列。
    """
    if not codes:
        return "\033[0m"
    return f"\033[{';'.join(str(c) for c in codes)}m"


def sgr_reset() -> str:
    """SGR 重置。

    Returns:
        ANSI SGR 重置序列。
    """
    return "\033[0m"


def fg_256(color: int) -> str:
    """设置 256 色前景色。

    Args:
        color: 256 色号 (0-255)。

    Returns:
        ANSI SGR 序列。
    """
    return f"\033[38;5;{color}m"


def bg_256(color: int) -> str:
    """设置 256 色背景色。

    Args:
        color: 256 色号 (0-255)。

    Returns:
        ANSI SGR 序列。
    """
    return f"\033[48;5;{color}m"


def fg_truecolor(r: int, g: int, b: int) -> str:
    """设置 24-bit 前景色。

    Args:
        r: 红色通道 (0-255)。
        g: 绿色通道 (0-255)。
        b: 蓝色通道 (0-255)。

    Returns:
        ANSI 24-bit SGR 序列。
    """
    return f"\033[38;2;{r};{g};{b}m"


def bg_truecolor(r: int, g: int, b: int) -> str:
    """设置 24-bit 背景色。

    Args:
        r: 红色通道 (0-255)。
        g: 绿色通道 (0-255)。
        b: 蓝色通道 (0-255)。

    Returns:
        ANSI 24-bit SGR 序列。
    """
    return f"\033[48;2;{r};{g};{b}m"


# ═══════════════════════════════════════════════════════════
# 窗口标题
# ═══════════════════════════════════════════════════════════

def set_window_title(title: str) -> None:
    """设置终端窗口标题。

    通过 OSC 序列 ``\\033]0;title\\007`` 设置。
    直接写入 ``sys.__stdout__``。

    Args:
        title: 窗口标题。
    """
    try:
        sys.__stdout__.write(f"\033]0;{title}\007")
        sys.__stdout__.flush()
    except (OSError, ValueError):
        pass


# ═══════════════════════════════════════════════════════════
# 便捷组合函数
# ═══════════════════════════════════════════════════════════

def write_stdout(data: str) -> None:
    """直接写入 ``sys.__stdout__``。

    用于紧急路径输出，绕过渲染管线。

    Args:
        data: 要写入的字符串。
    """
    try:
        sys.__stdout__.write(data)
        sys.__stdout__.flush()
    except (OSError, ValueError):
        pass


# ═══════════════════════════════════════════════════════════
# SIGWINCH 信号处理
# ═══════════════════════════════════════════════════════════

_sigwinch_callbacks: list[Callable[[int, int], None]] = []
_sigwinch_registered: bool = False


def register_sigwinch_callback(cb: Callable[[int, int], None]) -> None:
    """注册 SIGWINCH 回调。

    窗口尺寸变化时，回调被调用并传入 (width, height)。

    Args:
        cb: 回调函数，签名为 ``(width: int, height: int) -> None``。
    """
    global _sigwinch_registered
    if cb not in _sigwinch_callbacks:
        _sigwinch_callbacks.append(cb)
    if not _sigwinch_registered:
        try:
            signal.signal(signal.SIGWINCH, _handle_sigwinch)
            _sigwinch_registered = True
        except (OSError, ValueError):
            pass


def unregister_sigwinch_callback(cb: Callable[[int, int], None]) -> None:
    """取消注册 SIGWINCH 回调。

    Args:
        cb: 之前注册的回调函数。
    """
    try:
        _sigwinch_callbacks.remove(cb)
    except ValueError:
        pass


def _handle_sigwinch(signum: int, frame: object) -> None:
    """SIGWINCH 信号处理器。

    注意：信号处理器中不得使用 logging（非信号安全）。
    所有回调调用须包裹 try/except 防止单个回调崩溃中断其他回调。

    Args:
        signum: 信号编号。
        frame: 当前栈帧（未使用）。
    """
    width, height = _get_terminal_size()
    for cb in _sigwinch_callbacks:
        try:
            cb(width, height)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# TerminalWidthCache — 终端宽度缓存（兼容旧 API）
# ═══════════════════════════════════════════════════════════

class TerminalWidthCache:
    """终端宽度缓存 — 惰性查询 + 主动失效。

    提供与旧 ``terminal/terminal.py`` 中同名的兼容实现，
    使用 ``_get_terminal_size()`` 替代 blessed Terminal。
    """

    _instance: TerminalWidthCache | None = None

    def __init__(self) -> None:
        self._width: int = 0
        self._height: int = 0
        self._refresh()

    def _refresh(self) -> None:
        """刷新缓存的终端尺寸。"""
        self._width, self._height = _get_terminal_size()

    def get_width(self) -> int:
        """获取终端宽度，每次调用时惰性刷新。

        Returns:
            终端列数。
        """
        self._refresh()
        return self._width

    def get_height(self) -> int:
        """获取终端高度，每次调用时惰性刷新。

        Returns:
            终端行数。
        """
        self._refresh()
        return self._height

    @classmethod
    def get_default(cls) -> TerminalWidthCache:
        """获取全局单例。

        Returns:
            TerminalWidthCache 单例。
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ═══════════════════════════════════════════════════════════
# narrow_sep_width — 窄屏分隔线宽度（兼容旧 API）
# ═══════════════════════════════════════════════════════════

def narrow_sep_width(width: int | None = None, threshold: int = 40) -> int:
    """计算窄屏分隔线宽度。

    当终端宽度 < threshold 时使用缩短的宽度。

    Args:
        width: 终端宽度，None 时自动获取。
        threshold: 窄屏阈值。

    Returns:
        分隔线宽度。
    """
    if width is None:
        width, _ = _get_terminal_size()
    if width < threshold:
        return max(10, width - 2)
    return width
