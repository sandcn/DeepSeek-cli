"""终端 I/O 层 — 合并 _locked_terminal + narrow 宽度检测

统一管理：
  1. 终端宽度检测（TTL 缓存，减少 syscall）— 使用 Blessed Terminal
  2. output_lock 保护的终端写入上下文管理器 LockedTerminal

设计原则：
  - LockedTerminal 作为上下文管理器统一处理锁+光标+I/O
  - 终端宽度 TTL 缓存 0.5s，减少 10Hz tick 循环中 syscall 开销
  - Blessed 用于终端宽度查询和 ANSI 序列生成（非关键路径）
  - SCOSC/SCRC（光标保存/恢复）保留原始 ANSI（性能路径无需 Blessed）
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Self

from .adapter import TerminalAdapter
from .._locks import io_lock, OUTPUT_LOCK_TIMEOUT
from .blessed import get_terminal
from ..core.ttl_cache import TTLCache
import threading

_logger = logging.getLogger(__name__)


def _fetch_terminal_width() -> int:
    """获取终端宽度（列数），通过 Blessed Terminal，异常时回退 80。"""
    try:
        return get_terminal().width
    except Exception:
        return 80


def _fetch_terminal_height() -> int:
    """获取终端高度（行数），通过 Blessed Terminal，异常时回退 24。"""
    try:
        return get_terminal().height
    except Exception:
        return 24


# 终端宽度/高度 TTL 缓存实例（0.5s TTL，减少 10Hz tick 循环中 syscall 开销）
class TerminalWidthCache:
    """终端宽度缓存 — TTL 缓存，减少高频 ioctl/syscall。

    默认 0.5s TTL，适合 10Hz 渲染循环。
    提供类级单例访问，供全局统一使用。
    """

    _instance: "TerminalWidthCache | None" = None
    _instance_lock = threading.Lock()

    def __init__(self, ttl: float = 0.5) -> None:
        self._cache: TTLCache[int] = TTLCache(
            fetcher=_fetch_terminal_width, ttl=ttl,
        )
        self._height_cache: TTLCache[int] = TTLCache(
            fetcher=_fetch_terminal_height, ttl=ttl,
        )

    @classmethod
    def get_default(cls) -> "TerminalWidthCache":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def get_width(self) -> int:
        return self._cache.get()

    def refresh(self) -> int:
        return self._cache.refresh()

    def clear(self) -> None:
        self._cache.clear()
        self._height_cache.clear()

    def get_height(self) -> int:
        """获取终端高度（行数），TTL 缓存。"""
        return self._height_cache.get()

    def refresh_height(self) -> int:
        """强制刷新终端高度缓存（忽略 TTL）。"""
        return self._height_cache.refresh()

    def get_dimensions(self) -> tuple[int, int]:
        """一次调用获取终端 (宽度, 高度)，TTL 缓存。"""
        return (self.get_width(), self.get_height())

    def force_refresh(self) -> None:
        """强制刷新宽度和高度缓存（绕过 TTL），供 SIGWINCH 回调使用。"""
        self.refresh()
        self.refresh_height()


_width_cache = TerminalWidthCache.get_default()


# ═══════════════════════════════════════════════════════════
# 终端宽度检测（TTL 缓存）
# ═══════════════════════════════════════════════════════════

NARROW_THRESHOLD = 80
EXTRA_NARROW_THRESHOLD = 50


def get_terminal_width() -> int:
    return _width_cache.get_width()


def set_narrow_threshold(normal: int, extra: int) -> None:
    """允许用户按需调整窄屏阈值（全局生效）。

    修改后所有窄屏检测函数（is_narrow / narrow_truncate 等）
    立即使用新阈值。建议在应用初始化阶段调用一次。

    Args:
        normal: 普通窄屏阈值（列数），< 此值视为窄屏，默认 80。
        extra: 超窄屏阈值（列数），< 此值视为超窄屏，默认 50。
    """
    global NARROW_THRESHOLD, EXTRA_NARROW_THRESHOLD
    NARROW_THRESHOLD = normal
    EXTRA_NARROW_THRESHOLD = extra


# ═══════════════════════════════════════════════════════════
# ILockedTerminal — 终端写入端口（Protocol）
# ═══════════════════════════════════════════════════════════

@runtime_checkable
class ILockedTerminal(Protocol):
    """带 io_lock 保护的终端写入上下文管理器。"""
    def __enter__(self) -> "ILockedTerminal": ...
    def __exit__(self, *args: object) -> None: ...
    def __bool__(self) -> bool: ...
    def write(self, text: str) -> None: ...
    def writelines(self, lines: list[str]) -> None: ...


# ═══════════════════════════════════════════════════════════
# LockedTerminal — 带 output_lock 保护的终端写入
# ═══════════════════════════════════════════════════════════


class LockedTerminal(ILockedTerminal):
    """带 io_lock 保护的终端写入上下文管理器。

    特性：
    - 自动获取/释放 io_lock（超时时降级跳过）
    - 可选光标保存/恢复（save_cursor=True，默认）
    - 退出时自动 flush，消除中间闪烁
    - 提供 write() / writelines() 便捷方法

    设计为上下文管理器（__enter__/__exit__），布尔求值反映锁获取状态。

    注：LockedTerminal 使用 io_lock（终端 I/O 专用锁），与 render_lock
    （渲染管线锁）独立，两锁互不阻塞，提升并发吞吐。
    """

    def __init__(
        self,
        terminal: TerminalAdapter,
        save_cursor: bool = True,
        timeout: float = OUTPUT_LOCK_TIMEOUT,
    ) -> None:
        self._terminal = terminal
        self._save_cursor = save_cursor
        self._timeout = timeout
        self._acquired: bool = False

    def __enter__(self) -> Self:
        self._acquired = io_lock.acquire(timeout=self._timeout)
        if self._acquired:
            if self._save_cursor:
                try:
                    sc = get_terminal().sc
                    if not isinstance(sc, str) or not sc:
                        sc = "\033[s"
                    self._terminal.write_raw(sc)
                except Exception:
                    self._terminal.write_raw("\033[s")
        else:
            _logger.debug(
                "LockedTerminal io_lock 超时（%.1fs），降级跳过",
                self._timeout,
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if not self._acquired:
            return
        try:
            if self._save_cursor:
                try:
                    rc = get_terminal().rc
                    if not isinstance(rc, str) or not rc:
                        rc = "\033[u"
                    self._terminal.write_raw(rc)
                except Exception:
                    self._terminal.write_raw("\033[u")
            self._terminal.flush()
        finally:
            io_lock.release()

    def __bool__(self) -> bool:
        return self._acquired

    def write(self, text: str) -> None:
        """写入文本到终端（不 flush，由 __exit__ 统一 flush）。

        锁未获取时静默跳过（降级行为：不输出但也不抛异常）。
        """
        if not self._acquired:
            return
        self._terminal.write_raw(text)

    def writelines(self, lines: list[str]) -> None:
        """批量写入多行文本到终端（不 flush）。

        锁未获取时静默跳过（降级行为：不输出但也不抛异常）。
        """
        if not self._acquired:
            return
        for line in lines:
            self._terminal.write_raw(line)

    def __repr__(self) -> str:
        return (
            f"LockedTerminal(acquired={self._acquired}, "
            f"save_cursor={self._save_cursor}, timeout={self._timeout})"
        )


# ═══════════════════════════════════════════════════════════
# 窄屏自适应函数（从 narrow.py 合并）
# ═══════════════════════════════════════════════════════════


def is_narrow() -> bool:
    """当前终端是否为窄屏（< 80 列）"""
    return get_terminal_width() < NARROW_THRESHOLD


def _narrow_default(normal: int) -> int:
    return max(25, normal // 2)


def _extra_narrow_default(normal: int) -> int:
    return max(15, normal // 4)


def narrow_truncate(normal: int, narrow: int | None = None,
                    extra_narrow: int | None = None) -> int:
    w = get_terminal_width()
    if w >= NARROW_THRESHOLD:
        return normal
    if w >= EXTRA_NARROW_THRESHOLD:
        return narrow if narrow is not None else _narrow_default(normal)
    return extra_narrow if extra_narrow is not None else _extra_narrow_default(normal)


def narrow_indent(normal: int = 2) -> int:
    w = get_terminal_width()
    if w >= NARROW_THRESHOLD:
        return normal
    if w >= EXTRA_NARROW_THRESHOLD:
        return max(1, normal - 1)
    return 0


def narrow_sep_width(max_width: int = 40) -> int:
    tw = get_terminal_width()
    if tw >= NARROW_THRESHOLD:
        return min(max_width, tw - 4)
    return max(10, min(max_width - 10, tw - 4))


# ═══════════════════════════════════════════════════════════
# Raw Mode 保护（从 picker.py 提取，终端控制职责归入本层）
# ═══════════════════════════════════════════════════════════


def _try_set_raw(fd: int) -> dict | None:
    """尝试在指定 fd 上设置 raw mode，返回 guard 或 None。"""
    from src._compat_termios import termios as _tio, tty as _tty
    try:
        old = _tio.tcgetattr(fd)
        _tty.setraw(fd)
        # 验证：读取设置确认 ECHO 已关闭
        verify = _tio.tcgetattr(fd)
        if verify[3] & _tio.ECHO:
            _logger.warning(
                "raw mode verify FAILED (ECHO still on, fd=%d), retrying...", fd,
            )
            _tty.setraw(fd)
            verify2 = _tio.tcgetattr(fd)
            if verify2[3] & _tio.ECHO:
                _logger.error(
                    "raw mode verify FAILED after retry (fd=%d), restoring termios", fd,
                )
                # 恢复原始 termios，避免留下不一致的终端状态
                _tio.tcsetattr(fd, _tio.TCSADRAIN, old)
                return None
        return {"fd": fd, "old": old}
    except Exception as e:
        _logger.debug("_try_set_raw failed on fd=%d: %s", fd, e)
        return None


def _try_stdin_raw() -> dict | None:
    """策略1：使用 sys.stdin.fileno() 设置 raw mode。"""
    import os as _os
    import sys as _sys
    try:
        fd = _sys.stdin.fileno()
        if _os.isatty(fd):
            guard = _try_set_raw(fd)
            if guard is not None:
                _logger.debug("raw mode guard activated (fd=%d)", fd)
                return guard
    except Exception as e:
        _logger.debug("stdin.fileno() failed: %s", e)
    return None


def _try_tty_raw() -> dict | None:
    """策略2：打开 /dev/tty 设置 raw mode（备用 fallback）。"""
    import os as _os
    try:
        tty_fd = _os.open("/dev/tty", _os.O_RDWR)
        if _os.isatty(tty_fd):
            guard = _try_set_raw(tty_fd)
            if guard is not None:
                guard["need_close"] = True
                _logger.debug("raw mode guard activated via /dev/tty (fd=%d)", tty_fd)
                return guard
        _os.close(tty_fd)
    except Exception as e:
        _logger.debug("/dev/tty fallback failed: %s", e)
    return None


def enter_raw_mode() -> dict | None:
    """显式设置 stdin 为 raw mode，兜底保护。

    在 Termux/Android 等环境下，prompt_toolkit 内部的 raw mode 设置
    可能因各种原因被静默吞掉，导致终端保持在 cooked mode。

    策略：
      1. 先用 sys.stdin.fileno() 尝试设置 raw mode（_try_stdin_raw）
      2. 如果失败，尝试打开 /dev/tty 作为备用 fd（_try_tty_raw）

    Returns:
        保存的原始 termios 属性（用于恢复），或 None（设置失败）。
    """
    guard = _try_stdin_raw()
    if guard is not None:
        return guard
    guard = _try_tty_raw()
    if guard is not None:
        return guard
    _logger.warning("raw mode guard failed (all methods)")
    return None


def leave_raw_mode(guard: dict | None) -> None:
    """恢复原始终端属性，并关闭备用 fd。

    Args:
        guard: enter_raw_mode 的返回值，None 表示无需恢复。
    """
    if guard is None:
        return
    try:
        from src._compat_termios import termios as _termios
        _termios.tcsetattr(
            guard["fd"], _termios.TCSADRAIN, guard["old"],
        )
        _logger.debug("raw mode guard restored (fd=%d)", guard["fd"])
    except Exception as e:
        _logger.warning("raw mode guard restore failed: %s", e)
    finally:
        if guard.get("need_close"):
            import os as _os
            try:
                _os.close(guard["fd"])
            except Exception:
                _logger.debug("raw mode guard fd 关闭失败")


__all__ = [
    "get_terminal_width",
    "TerminalWidthCache",
    "LockedTerminal",
    "is_narrow",
    "narrow_truncate", "narrow_indent",
    "narrow_sep_width",
    # 常量（供 narrow.py 兼容导出）
    "set_narrow_threshold",
    "NARROW_THRESHOLD",
    "EXTRA_NARROW_THRESHOLD",
    # raw mode 保护
    "enter_raw_mode",
    "leave_raw_mode",
]
