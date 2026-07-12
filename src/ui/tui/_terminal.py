"""终端 I/O 层 — 合并 _locked_terminal + narrow 宽度检测

统一管理：
  1. 终端宽度检测（TTL 缓存，减少 syscall）— 使用 Blessed Terminal
  2. output_lock 保护的终端写入上下文管理器 LockedTerminal
  3. 终端标题动态更新 — 流式输出期间显示进度信息
  4. 终端光标样式控制（DECSCUSR）
  5. Raw mode 保护 — 确保终端输入处于 raw mode

设计原则：
  - LockedTerminal 作为上下文管理器统一处理锁+光标+I/O
  - 终端宽度 TTL 缓存 0.5s，减少 10Hz tick 循环中 syscall 开销
  - Blessed 用于终端宽度查询和 ANSI 序列生成（非关键路径）
  - SCOSC/SCRC（光标保存/恢复）保留原始 ANSI（性能路径无需 Blessed）

终端标题功能：
  - set_title(): 设置终端标题（OSC 转义序列）
  - flash_title(): 临时闪烁标题后恢复
  - update_title_with_progress(): 流式输出期间动态显示模型名/Token数/速率
  - restore_default_title(): 恢复默认标题"DeepSeek"
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Self

from ..terminal_adapter import TerminalAdapter
from .._lock import output_lock, OUTPUT_LOCK_TIMEOUT
from .._blessed import get_terminal
from ._ttl_cache import TTLCache

_logger = logging.getLogger(__name__)


def _fetch_terminal_width() -> int:
    """获取终端宽度（列数），通过 Blessed Terminal，异常时回退 80。"""
    try:
        return get_terminal().width
    except Exception:
        return 80


# 终端宽度 TTL 缓存实例（0.5s TTL，减少 10Hz tick 循环中 syscall 开销）
_term_width_cache: TTLCache[int] = TTLCache(
    fetcher=_fetch_terminal_width, ttl=0.5,
)


# ═══════════════════════════════════════════════════════════
# 终端宽度检测（TTL 缓存）
# ═══════════════════════════════════════════════════════════

NARROW_THRESHOLD = 80
EXTRA_NARROW_THRESHOLD = 50


def get_terminal_width() -> int:
    return _term_width_cache.get()


# ═══════════════════════════════════════════════════════════
# ILockedTerminal — 终端写入端口（Protocol）
# ═══════════════════════════════════════════════════════════

@runtime_checkable
class ILockedTerminal(Protocol):
    """带 output_lock 保护的终端写入上下文管理器。"""
    def __enter__(self) -> "ILockedTerminal": ...
    def __exit__(self, *args: object) -> None: ...
    def __bool__(self) -> bool: ...
    def write(self, text: str) -> None: ...
    def writelines(self, lines: list[str]) -> None: ...


# ═══════════════════════════════════════════════════════════
# LockedTerminal — 带 output_lock 保护的终端写入
# ═══════════════════════════════════════════════════════════


class LockedTerminal(ILockedTerminal):
    """带 output_lock 保护的终端写入上下文管理器。

    特性：
    - 自动获取/释放 output_lock（超时时降级跳过）
    - 可选光标保存/恢复（save_cursor=True，默认）
    - 退出时自动 flush，消除中间闪烁
    - 提供 write() / writelines() 便捷方法

    设计为上下文管理器（__enter__/__exit__），布尔求值反映锁获取状态。
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
        self._acquired = output_lock.acquire(timeout=self._timeout)
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
                "LockedTerminal output_lock 超时（%.1fs），降级跳过",
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
            output_lock.release()

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
    import termios as _tio
    import tty as _tty
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
        import termios as _termios
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


# ═══════════════════════════════════════════════════════════
# 终端光标样式（DECSCUSR）
# ═══════════════════════════════════════════════════════════

_CURSOR_STYLE_MAP: dict[str, str] = {
    "blinking_block":       "\033[1 q",
    "steady_block":         "\033[2 q",
    "blinking_underline":   "\033[3 q",
    "steady_underline":     "\033[4 q",
    "blinking_bar":         "\033[5 q",
    "steady_bar":           "\033[6 q",
}
"""DECSCUSR 光标样式 ANSI 序列映射表。

标准 xterm 兼容终端支持，Cygwin/Mintty 可能不支持某些样式。
默认使用闪烁方块（blinking_block）。
"""


def set_cursor_style(style: str = "blinking_block") -> None:
    """使用 DECSCUSR 设置终端光标形状。

    Args:
        style: 光标样式名称，可选值：
            - "blinking_block"（默认）：闪烁方块
            - "steady_block"：稳定方块
            - "blinking_underline"：闪烁下划线
            - "steady_underline"：稳定下划线
            - "blinking_bar"：闪烁竖线
            - "steady_bar"：稳定竖线

    注意：并非所有终端模拟器都支持所有样式。
    不支持的终端静默忽略该序列。
    """
    import sys
    ansi = _CURSOR_STYLE_MAP.get(style, "\033[1 q")
    sys.__stdout__.write(ansi)
    sys.__stdout__.flush()


def reset_cursor_style() -> None:
    """恢复默认光标形状（闪烁方块）。"""
    set_cursor_style("blinking_block")


# ═══════════════════════════════════════════════════════════
# 终端标题（OSC 转义序列）
# ═══════════════════════════════════════════════════════════

_DEFAULT_TITLE = "DeepSeek"


def set_title(title: str) -> None:
    """使用 OSC 转义序列设置终端标题。

    Args:
        title: 要设置的标题文本。
    """
    import sys
    # OSC 0; title ST — 设置窗口和图标标题
    sys.__stdout__.write(f"\033]0;{title}\007")
    sys.__stdout__.flush()


def flash_title(text: str, duration: float = 2.0) -> None:
    """临时闪烁终端标题，随后恢复默认标题。

    适合长时间工具调用完成后、收到新消息时的视觉反馈。

    Args:
        text: 闪烁时显示的标题文本。
        duration: 闪烁持续时间（秒）。
    """
    import sys
    import threading
    # 保存当前标题
    current = _DEFAULT_TITLE
    # 设置闪烁标题
    sys.__stdout__.write(f"\033]0;{text}\007")
    sys.__stdout__.flush()
    # 延迟恢复
    def _restore():
        import time as _t
        _t.sleep(duration)
        sys.__stdout__.write(f"\033]0;{current}\007")
        sys.__stdout__.flush()
    threading.Thread(target=_restore, daemon=True).start()


# ═══════════════════════════════════════════════════════════
# 终端标题动态更新（流式进度）
# ═══════════════════════════════════════════════════════════

_title_cache: dict[str, Any] = {}
"""终端标题更新节流缓存（避免高频 OSC 序列）。

_key: (model, tokens, speed_rounded) 元组，用于内容级节流。
"""

_title_is_default: bool = True
"""标题是否处于默认状态，用于 restore_default_title 节流。"""


def update_title_with_progress(
    model: str, tokens: int, speed: float, elapsed: float,
) -> None:
    """更新终端标题为流式进度信息。

    格式: ``DeepSeek | {model} | ⚡ {tokens}t | {speed:.1f}t/s | ⟳``

    使用 ``set_title()`` 实现，每帧调用更新。
    节流：同一进度快照下不重复写入（避免高频 OSC 序列）。
    设计模式: 外观模式 — 封装 ``set_title`` 原始调用，对外提供语义化接口。

    Args:
        model: 模型名称。
        tokens: 已生成的 token 数。
        speed: 当前生成速度（tok/s）。
        elapsed: 已耗时（秒）。

    注意：部分终端模拟器对高频 OSC 序列支持不佳，节流机制可缓解。
    """
    global _title_is_default
    try:
        # 构建标题文本
        if speed > 0:
            speed_str = f"{speed:.1f}"
        else:
            speed_str = "?"
        title = f"DeepSeek | {model} | ⚡ {tokens}t | {speed_str}t/s | ⟳"

        # 内容级节流：同一进度值不重复写入
        cache_key = (model, tokens, round(speed, 1))
        if _title_cache.get("key") == cache_key:
            _logger.debug("update_title_with_progress 节流跳过 (model=%s, tokens=%d, speed=%.1f)", model, tokens, speed)
            return
        _title_cache["key"] = cache_key

        _title_is_default = False
        set_title(title)
    except Exception:
        _logger.debug("update_title_with_progress 失败（静默降级）")


def restore_default_title() -> None:
    """恢复终端标题为默认值。

    委托 ``set_title(_DEFAULT_TITLE)``。
    节流：仅在标题非默认状态时写入。
    """
    global _title_is_default
    if _title_is_default:
        return
    _title_is_default = True
    set_title(_DEFAULT_TITLE)


__all__ = [
    "get_terminal_width",
    "LockedTerminal",
    "is_narrow",
    "narrow_truncate", "narrow_indent",
    "narrow_sep_width",
    # 常量（供 narrow.py 兼容导出）
    "NARROW_THRESHOLD",
    "EXTRA_NARROW_THRESHOLD",
    # raw mode 保护
    "enter_raw_mode",
    "leave_raw_mode",
    # 光标样式
    "set_cursor_style",
    "reset_cursor_style",
    # 终端标题
    "set_title",
    "flash_title",
    "update_title_with_progress",
    "restore_default_title",
    "_DEFAULT_TITLE",
]
