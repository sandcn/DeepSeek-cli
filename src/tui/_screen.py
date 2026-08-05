"""纯 ANSI 终端屏幕管理 — 零第三方依赖。

提供终端尺寸查询、字符宽度计算、ANSI 转义序列生成、
SIGWINCH 信号处理等基础设施。所有函数为纯字符串返回或
直接写入 ``sys.__stdout__``，不依赖 blessed/wcwidth 等第三方库。

设计模式: 外观（Facade）— 作为所有终端 I/O 的统一入口。

遗留标注（2026-07-31 方向F）：鼠标输入不支持 / bracketed paste 无协议——
功能增强项，不在本次架构改进范围，**标记 P2 遗留**（后续如需鼠标支持须引入
终端能力协商与协议解析，评估后再实施）。

模块边界（2026-08-05 架构优化）：字符显示宽度计算（CJK/Emoji/零宽/ANSI 跳过/
单字符缓存）已拆分至 ``_width.py``（纯计算职责，Layer 0 零依赖）；本模块聚焦
终端 I/O——尺寸查询/ANSI 序列/SIGWINCH/TerminalWidthCache，宽度符号经
re-export 保持旧导入路径兼容。
"""

from __future__ import annotations

import fcntl
import io
import os
import signal
import struct
import sys
import termios
import threading
import time
from typing import Callable

# 字符显示宽度计算已拆分至 _width.py（纯计算职责，Layer 0 零依赖）；
# 本模块 re-export 保持旧导入路径兼容（test_screen.py / ink.helpers 等）。
from ._width import (
    wcswidth_simple,
    _CJK_RANGES,
    _ZERO_WIDTH_RANGES,
    _FULLWIDTH_RANGES,
    _EMOJI_WIDE_RANGES,
    _build_flat_ranges,
    _in_ranges_bisect,
    _CJK_FLAT,
    _FULLWIDTH_FLAT,
    _EMOJI_WIDE_FLAT,
    _ZERO_WIDTH_FLAT,
    _ASCII_RUN_RE,
    _skip_ansi_at,
    _CHAR_WIDTH_CACHE_MAX,
    _char_width_cache,
    _wcswidth_single,
)

# ★ 标准 React Ink 组件化（2026-08-05）：原 ANSI 颜色常量（_COLOR_*）re-export
# 已移除——生产渲染统一用 core/style.py Style（fg 色号），色号从
# _const._SEMANTIC_COLOR 槽位表解析（零视觉回归）。ANSI_EMERGENCY_*（紧急
# 路径）保留于 _const。


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
# 终端能力协商
# ═══════════════════════════════════════════════════════════

def detect_truecolor() -> bool:
    """检测终端是否支持 truecolor（24-bit 颜色）。

    方向3（单一真源收敛）：复用 ``core.color`` 的判定逻辑（含 NO_COLOR 强制
    降级 + TERM direct 判定）——修复前本模块独立实现（仅查 ``COLORTERM``，
    不尊重 ``NO_COLOR`` 规范），与 ``core/color`` 双实现语义漂移。此处调用
    ``_detect_truecolor_uncached``（无进程级缓存），保持本模块既有「每次
    独立检测」语义（test_screen 锁定，避免 core/color 缓存跨测试污染）。

    Returns:
        True — 终端宣称支持 truecolor；False — 默认 256 色降级。
    """
    from src.tui.core.color import _detect_truecolor_uncached
    return _detect_truecolor_uncached()


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


# ═══════════════════════════════════════════════════════════
# 清屏/清行
# ═══════════════════════════════════════════════════════════

def clear_line() -> str:
    """清除从光标到行尾的内容 (EL 0)。

    Returns:
        ANSI EL 序列。
    """
    return "\r\033[K"


def clear_screen_from_cursor() -> str:
    """清除从光标到屏幕末尾 (ED 0)。

    Returns:
        ANSI ED 序列。
    """
    return "\033[0J"


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
    except (OSError, ValueError, AttributeError):  # BUG-52：无 TTY 时 stdout 为 None
        pass


# ═══════════════════════════════════════════════════════════
# SIGWINCH 信号处理
# ═══════════════════════════════════════════════════════════

_sigwinch_callbacks: list[Callable[[int, int], None]] = []
_sigwinch_registered: bool = False
# BUG-T4：信号处理器只置标志（信号安全），渲染循环经 process_sigwinch() 消费
_sigwinch_pending: bool = False


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


def _handle_sigwinch(signum: int, frame: object) -> None:
    """SIGWINCH 信号处理器 — 仅置标志（信号安全）。

    BUG-T4：信号处理器中禁止调用非信号安全操作（fcntl.ioctl / Event.set /
    用户回调 / logging）。终端尺寸刷新与回调执行迁移到 ``process_sigwinch()``，
    由渲染循环轮询调用。

    Args:
        signum: 信号编号。
        frame: 当前栈帧（未使用）。
    """
    global _sigwinch_pending
    _sigwinch_pending = True


def process_sigwinch() -> bool:
    """处理待处理的 SIGWINCH 事件（渲染线程轮询调用）。

    若信号处理器已置位 pending 标志，则复位标志并在**正常线程上下文**中
    刷新终端尺寸 + 遍历执行 SIGWINCH 回调（每个回调 try/except 隔离，
    防止单个回调崩溃中断其他回调）。

    Returns:
        True — 本帧有 SIGWINCH 待处理且已处理；
        False — 无待处理事件。
    """
    global _sigwinch_pending
    if not _sigwinch_pending:
        return False
    _sigwinch_pending = False
    try:
        width, height = _get_terminal_size()
    except Exception:
        width, height = 80, 24
    for cb in _sigwinch_callbacks:
        try:
            cb(width, height)
        except Exception:
            pass
    return True


# ═══════════════════════════════════════════════════════════
# TerminalWidthCache — 终端宽度缓存（TTL 惰性缓存 + 主动失效）
# ═══════════════════════════════════════════════════════════

class TerminalWidthCache:
    """终端宽度缓存 — TTL 惰性缓存 + 主动失效。

    提供与旧 ``terminal/terminal.py`` 中同名的兼容实现，
    使用 ``_get_terminal_size()`` 替代 blessed Terminal。

    设计模式: 装饰器（Decorator）— 在 ``_get_terminal_size()`` 之上
    添加 TTL 缓存层。
    """

    _instance: TerminalWidthCache | None = None

    def __init__(self, ttl: float = 60.0) -> None:
        """初始化缓存。

        Args:
            ttl: TTL 秒数（默认 60 秒）。get_width/get_height 在 TTL 内
                 返回缓存值，过期后调用 _get_terminal_size() 获取新值。
        """
        self._ttl = ttl
        self._width: int = 0
        self._height: int = 0
        self._last_width_fetch: float = 0.0
        self._last_height_fetch: float = 0.0
        self._fetch()

    def _fetch(self) -> None:
        """从底层获取终端尺寸并更新缓存。"""
        try:
            self._width, self._height = _get_terminal_size()
        except Exception:
            self._width, self._height = 80, 24
        now = time.monotonic()
        self._last_width_fetch = now
        self._last_height_fetch = now

    def _is_expired(self, last_fetch: float) -> bool:
        """检查缓存是否过期（超过 TTL）。"""
        return (time.monotonic() - last_fetch) > self._ttl

    def get_width(self) -> int:
        """获取终端宽度（TTL 缓存）。"""
        if self._is_expired(self._last_width_fetch):
            try:
                w, h = _get_terminal_size()
                self._width = w
                self._height = h
            except Exception:
                self._width, self._height = 80, 24
            now = time.monotonic()
            self._last_width_fetch = now
            self._last_height_fetch = now
        return self._width

    def get_height(self) -> int:
        """获取终端高度（TTL 缓存）。"""
        if self._is_expired(self._last_height_fetch):
            try:
                w, h = _get_terminal_size()
                self._width = w
                self._height = h
            except Exception:
                self._width, self._height = 80, 24
            now = time.monotonic()
            self._last_width_fetch = now
            self._last_height_fetch = now
        return self._height

    def get_dimensions(self) -> tuple[int, int]:
        """获取终端尺寸 (宽度, 高度)。

        ★ 方向1（高度陈旧修复）：高度经 ``get_height()`` 走独立 TTL 检查——
        修复前直接读 ``_height`` 字段绕过 height TTL（width TTL 未过期时
        返回陈旧高度）。
        """
        # 先获取宽度（也会更新高度缓存）
        w = self.get_width()
        h = self.get_height()
        return (w, h)

    def force_refresh(self) -> None:
        """绕过 TTL 立即刷新宽度和高度。"""
        self._fetch()

    def clear(self) -> None:
        """清空缓存，下次查询强制刷新。"""
        self._last_width_fetch = 0.0
        self._last_height_fetch = 0.0

    def refresh_height(self) -> int:
        """强制刷新高度缓存，返回新高度。

        Returns:
            当前终端高度。
        """
        try:
            w, h = _get_terminal_size()
            self._width = w
            self._height = h
        except Exception:
            self._width, self._height = 80, 24
        now = time.monotonic()
        self._last_width_fetch = now
        self._last_height_fetch = now
        return self._height

    @classmethod
    def get_default(cls) -> TerminalWidthCache:
        """获取全局单例（双检锁——方向1 步骤1：并发首次调用不产生多实例）。

        多实例会各自 TTL 缓存导致宽度不一致（并发首次调用竞态）；双检锁为
        Python 标准模式（GIL 下安全）；实例已存在时无锁路径零开销。
        """
        if cls._instance is None:
            with _instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance


#: TerminalWidthCache 单例双检锁（get_default 并发首次调用互斥）
_instance_lock = threading.Lock()


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
        width = TerminalWidthCache.get_default().get_width()
    if width < threshold:
        return max(10, width - 2)
    return width
