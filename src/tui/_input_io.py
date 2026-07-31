"""InputIO — TUI 输入 I/O 读取层（提取自 _input.py，方向A 步骤1）。

将 Input 上帝类中的 stdin 读取原语与 I/O 状态机提取为独立类，逐行迁移，
保持零逻辑改动：
  - 读取原语: read_byte / read_with_timeout / read_utf8_char / try_read_paste
  - 残留排空: _flush_stdin_residual / flush_stdin_buffer
  - I/O 状态机: start_io / stop_io / pause_io / resume_io（_active / _stop / _interrupted）
  - 故障检测: _eof_count / _select_error_count / _exit_reason / _fd_status
  - 粘贴退避: _paste_skip_counter / _paste_skip_threshold

InputIO 持有 fd 与粘贴退避状态；``_interrupted`` 事件仍由 Input 公开属性
``interrupted`` 委托读取。``_UTF8_READ_TIMEOUT`` 从 _input_parser 导入。

设计模式: 单一职责（SRP）提取——读取层仅负责原始 I/O，不含缓冲/分发。

依赖方向:
  _input.py → _input_io.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` / ``import os`` 供读取方法使用；可被
``patch("select.select", ...)`` / ``patch("os.read", ...)`` 全局拦截
（与 _input.py 原行为等价）。
"""

from __future__ import annotations

import logging
import os
import select
import threading

from src._compat_termios import HAS_TERMIOS, termios
# P3-1 说明：从 escape_monitor._history 导入仅取常量（_EOF_THRESHOLD /
# _SELECT_ERROR_THRESHOLD），不导入历史 I/O 函数；阈值常量收敛在
# escape_monitor 模块（既有真源），不复制魔数。
from src.api.escape_monitor._history import (
    _EOF_THRESHOLD,
    _SELECT_ERROR_THRESHOLD,
)
from ._input_parser import _UTF8_READ_TIMEOUT

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# InputIO — stdin 原始读取 + I/O 状态机
# ═══════════════════════════════════════════════════════════

class InputIO:
    """stdin 原始读取层 + I/O 状态机。

    仅负责原始 I/O（读取/排空/粘贴检测）与 I/O 标志位管理，
    不含缓冲编辑与事件分发（分别由 InputBufferEditor / InputDispatcher 承担）。

    由 Render 线程通过 Input.read_stdin_once()（委托 InputDispatcher）驱动。
    """

    def __init__(self, fd: int) -> None:
        self._fd = fd

        # ── I/O 状态控制 ──
        self._io_started: bool = False
        self._active = threading.Event()
        self._active.set()
        self._stop = threading.Event()
        self._interrupted = threading.Event()

        # ── 粘贴退避优化 ──
        self._paste_skip_counter: int = 0
        self._paste_skip_threshold: int = 10

        # ── 故障检测 ──
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason: str | None = None
        self._fd_status: str = "ok"

    # ── 状态访问（供 Input / InputDispatcher 委托） ────────

    @property
    def fd(self) -> int:
        """stdin 文件描述符。"""
        return self._fd

    @fd.setter
    def fd(self, value: int) -> None:
        """设置 fd（供测试 patch 与装配调整）。"""
        self._fd = value

    @property
    def is_io_running(self) -> bool:
        """I/O 是否处于激活状态（标志位管理，非线程存活检测）。"""
        return self._io_started

    @property
    def interrupted(self) -> bool:
        """中断标志是否被设置。"""
        return self._interrupted.is_set()

    @property
    def active(self) -> threading.Event:
        """I/O 激活事件（供 InputDispatcher.read_stdin_once 状态检查）。"""
        return self._active

    @property
    def stop(self) -> threading.Event:
        """I/O 停止事件（供 InputDispatcher.read_stdin_once 状态检查）。"""
        return self._stop

    @property
    def fd_status(self) -> str:
        """stdin 状态（"ok" / "error"）。"""
        return self._fd_status

    @property
    def select_error_count(self) -> int:
        """select 连续错误计数。"""
        return self._select_error_count

    @property
    def eof_count(self) -> int:
        """EOF 连续计数。"""
        return self._eof_count

    @property
    def exit_reason(self) -> str | None:
        """退出原因（"eof" / "select_error" / None）。"""
        return self._exit_reason

    # ── 中断事件操作（供 Input / InputDispatcher 委托） ────

    def set_interrupted(self) -> None:
        """设置中断标志（_do_interrupt 使用）。"""
        self._interrupted.set()

    def clear_interrupted(self) -> None:
        """清除中断标志（start_io / reset 使用）。"""
        self._interrupted.clear()

    # ── 故障记录（供 InputDispatcher.read_stdin_once 委托） ─

    def can_read(self) -> bool:
        """是否可以执行读取（fd 状态 + 激活/停止标志检查）。

        与 _input.py 原 read_stdin_once 状态检查等价：
          - _fd_status == "error" → False
          - _active 未设置 或 _stop 已设置 → False
        """
        if self._fd_status == "error":
            return False
        if not self._active.is_set() or self._stop.is_set():
            return False
        return True

    def record_select_error(self) -> None:
        """记录一次 select 错误；连续达阈值判定 stdin 不可用。

        与 _input.py 原 read_stdin_once 异常分支等价（仅增量 + 阈值判定，
        不改变返回语义——调用方一律返回 False）。
        """
        self._select_error_count += 1
        if self._select_error_count >= _SELECT_ERROR_THRESHOLD:
            _logger.warning(
                "select 错误连续 %d 次，判定 stdin 不可用",
                self._select_error_count,
            )
            self._exit_reason = "select_error"
            self._fd_status = "error"

    def reset_select_error(self) -> None:
        """select 成功后清零错误计数。"""
        self._select_error_count = 0

    def record_eof(self) -> None:
        """记录一次 EOF；连续达阈值判定 pty 已断开。

        与 _input.py 原 read_stdin_once EOF 分支等价（仅增量 + 阈值判定，
        不改变返回语义——调用方一律返回 False）。
        """
        self._eof_count += 1
        if self._eof_count >= _EOF_THRESHOLD:
            _logger.warning(
                "stdin EOF 连续 %d 次，判定 pty 已断开",
                self._eof_count,
            )
            self._exit_reason = "eof"

    def reset_eof(self) -> None:
        """读取成功后清零 EOF 计数。"""
        self._eof_count = 0

    def mark_fd_error(self) -> None:
        """os.read 异常时将 fd 标记为不可用。"""
        self._fd_status = "error"

    # ═══════════════════════════════════════════════════════
    # I/O 状态管理
    # ═══════════════════════════════════════════════════════

    def start_io(self) -> None:
        """激活 I/O 读取（标志位管理模式，不再创建 daemon 线程）。

        stdin 读取由 render 线程通过 ``read_stdin_once()`` 驱动，
        此方法仅重置状态标志位。调用前应确保终端已设置为 cbreak 模式
        （由 EscapeMonitor 保证）。幂等：重复调用仅重置标志位。
        """
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        self._io_started = True
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason = None
        self._fd_status = "ok"

    def stop_io(self) -> None:
        """停用 I/O 读取（标志位管理模式，不再 join 线程）。

        设置 stop 和 active 标志位，render 线程中 ``read_stdin_once()``
        检测到后停止读取。幂等安全。
        """
        self._stop.set()
        self._active.set()  # 确保 read_stdin_once() 状态检查快速退出
        self._io_started = False
        self._fd_status = "ok"

    def pause_io(self) -> None:
        """暂停 I/O 读取（供 EscapeMonitor 的特殊按键回调使用）。

        暂停后 ``read_stdin_once()`` 在 render 线程中检测到 ``_active``
        未设置时跳过读取。
        """
        self._active.clear()

    def resume_io(self) -> None:
        """恢复 I/O 读取（供 EscapeMonitor 的特殊按键回调使用）。"""
        self._active.set()

    # ═══════════════════════════════════════════════════════
    # stdin 读取原语
    # ═══════════════════════════════════════════════════════

    def read_byte(self) -> bytes:
        """从 fd 读取单个原始字节。

        Returns:
            读取到的单字节 bytes 对象；EOF/错误时返回空 bytes。
        """
        try:
            return os.read(self._fd, 1)
        except (ValueError, OSError, TypeError):
            return b""

    def read_with_timeout(self, timeout: float) -> bytes | None:
        """使用 select + os.read 读取单个字节，超时返回 None。"""
        try:
            ready, _, _ = select.select([self._fd], [], [], timeout)
        except (ValueError, OSError, TypeError, AttributeError):
            return None
        if not ready:
            return None
        try:
            raw = os.read(self._fd, 1)
            return raw if raw else None
        except (ValueError, OSError, TypeError):
            return None

    def try_read_paste(self, fd: int, first_chars: str) -> str:
        """检测并读取粘贴内容（退避 select 检测突发字符流）。"""
        # 快速路径：若近期均非粘贴，跳过退避检测
        if self._paste_skip_counter >= self._paste_skip_threshold:
            try:
                has_more, _, _ = select.select([fd], [], [], 0.0)
            except (ValueError, OSError, TypeError, AttributeError):
                return first_chars
            if not has_more:
                return first_chars
            # 有数据，重置计数器并进入粘贴检测
            self._paste_skip_counter = 0
        else:
            for delay in (0.0001, 0.002, 0.003):
                try:
                    has_more, _, _ = select.select([fd], [], [], delay)
                except (ValueError, OSError, TypeError, AttributeError):
                    return first_chars
                if not has_more:
                    self._paste_skip_counter += 1
                    return first_chars
        extra = b""
        try:
            while True:
                has_more, _, _ = select.select([fd], [], [], 0.01)
                if not has_more:
                    break
                more = os.read(fd, 65536)
                if not more:
                    break
                extra += more
                if len(extra) >= 262144:
                    break
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        if not extra:
            return first_chars
        return first_chars + extra.decode("utf-8", errors="replace")

    def read_utf8_char(self, fd: int, first_byte: int) -> str | None:
        """读取完整的多字节 UTF-8 字符序列。"""
        if (first_byte & 0xE0) == 0xC0:
            total_bytes = 2
        elif (first_byte & 0xF0) == 0xE0:
            total_bytes = 3
        elif (first_byte & 0xF8) == 0xF0:
            total_bytes = 4
        else:
            return None

        buf = bytes([first_byte])
        for _ in range(total_bytes - 1):
            try:
                has_data, _, _ = select.select(
                    [fd], [], [], _UTF8_READ_TIMEOUT,
                )
            except (ValueError, OSError, TypeError, AttributeError):
                break
            if not has_data:
                break
            try:
                more = os.read(fd, 1)
                if not more:
                    break
                buf += more
            except (ValueError, OSError, TypeError):
                break

        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            return None

    def _flush_stdin_residual(self, max_flush: int = 50) -> None:
        """非阻塞清理 stdin 残留字节。"""
        if self._fd_status == "error":
            return
        flushed = 0
        while flushed < max_flush:
            if self._stop.is_set():
                return
            try:
                ready, _, _ = select.select([self._fd], [], [], 0.05)
                if not ready:
                    break
                os.read(self._fd, 1)
                flushed += 1
            except (ValueError, OSError, TypeError, AttributeError):
                _logger.debug("排空 stdin 残留时异常", exc_info=True)
                break

    def flush_stdin_buffer(self, max_flush: int = 50) -> None:
        """公开方法：非阻塞清理 stdin 残留字节 + termios 缓冲区刷洗。

        先使用 select 排空可读字节（委托 _flush_stdin_residual），
        再通过 tcflush 刷洗内核输入队列（仅在 HAS_TERMIOS=True 时执行）。

        Args:
            max_flush: 最大排空字节数限制（传递给 _flush_stdin_residual）。
        """
        self._flush_stdin_residual(max_flush)
        if HAS_TERMIOS:
            try:
                termios.tcflush(self._fd, termios.TCIFLUSH)
            except Exception:
                _logger.debug("tcflush 失败", exc_info=True)


__all__ = ["InputIO"]
