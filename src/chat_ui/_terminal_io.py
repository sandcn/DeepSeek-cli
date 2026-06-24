"""TerminalIO — 统一终端 I/O 抽象层。

封装所有终端写入操作，消除散落在各模块中的 sys.__stdout__ 直接调用。
内部处理 Blessed/原始 ANSI 降级、output_lock 管理、DECSTBM 和光标操作。

职责：
  - write / write_raw: 文本输出
  - flush: 刷新缓冲区
  - move_cursor / clear_line: 光标控制
  - set_scroll_region / reset_scroll_region: DECSTBM
  - get_size: 终端尺寸查询
"""

from __future__ import annotations

import logging
import sys
import threading
from contextlib import contextmanager
from typing import TextIO

_logger = logging.getLogger(__name__)


class TerminalIO:
    """终端 I/O 统一抽象。

    封装：
      - sys.__stdout__ 写入
      - Blessed/原始 ANSI 降级
      - output_lock 管理
      - 光标和 DECSTBM 操作

    使用方式：
        tio = TerminalIO()
        tio.write("hello")
        tio.move_cursor(10, 5)
        tio.flush()
    """

    def __init__(self, lock: threading.RLock | None = None, stream: TextIO | None = None):
        self._stream = stream if stream is not None else sys.__stdout__
        self._lock = lock  # 外部注入的 output_lock

        # Blessed 惰性初始化
        self._blessed_term = None
        self._blessed_available: bool | None = None

    # ── 基本 I/O ──────────────────────────────────────

    def write(self, text: str) -> None:
        """写入文本到终端。"""
        self._stream.write(str(text))

    def write_raw(self, text: str) -> None:
        """直写 ANSI 序列（不经过 Rich/Renderer 处理）。"""
        self._stream.write(text)

    def flush(self) -> None:
        """刷新输出缓冲区。"""
        self._stream.flush()

    # ── 光标控制 ──────────────────────────────────────

    def move_cursor(self, row: int, col: int) -> None:
        """移动光标到指定行列（1-based）。"""
        try:
            term = self._get_blessed()
            result = term.move_xy(col - 1, row - 1)
            seq = result if result else f"\033[{row};{col}H"
        except Exception:
            seq = f"\033[{row};{col}H"
        self._stream.write(seq)

    def clear_line(self, row: int) -> None:
        """清除指定行（1-based）。"""
        try:
            term = self._get_blessed()
            result = term.move_xy(0, row - 1) + term.clear_eol()
            seq = result if result else f"\033[{row};1H\033[K"
        except Exception:
            seq = f"\033[{row};1H\033[K"
        self._stream.write(seq)

    def save_cursor(self) -> None:
        """保存光标位置。"""
        try:
            sc = self._get_blessed().sc
            seq = sc if isinstance(sc, str) and sc else "\0337"
        except Exception:
            seq = "\0337"
        self._stream.write(seq)

    def restore_cursor(self) -> None:
        """恢复光标位置。"""
        try:
            rc = self._get_blessed().rc
            seq = rc if isinstance(rc, str) and rc else "\0338"
        except Exception:
            seq = "\0338"
        self._stream.write(seq)

    # ── DECSTBM ──────────────────────────────────────

    def set_scroll_region(self, top: int, bottom: int) -> None:
        """设置 DECSTBM 滚动区域（1-based）。"""
        try:
            term = self._get_blessed()
            seq = term.csr(top - 1, bottom - 1)
            result = seq if isinstance(seq, str) and seq else f"\033[{top};{bottom}r"
        except Exception:
            result = f"\033[{top};{bottom}r"
        self._stream.write(result)

    def reset_scroll_region(self) -> None:
        """重置 DECSTBM 为全屏。"""
        self._stream.write("\033[r")

    def scroll_up(self, n: int) -> None:
        """向上滚动 n 行（SU）。"""
        if n <= 0:
            return
        try:
            seq = self._get_blessed().indn(n)
            result = seq if isinstance(seq, str) and seq else f"\033[{n}S"
        except Exception:
            result = f"\033[{n}S"
        self._stream.write(result)

    def scroll_down(self, n: int) -> None:
        """向下滚动 n 行（SD/RI）。"""
        if n <= 0:
            return
        try:
            seq = self._get_blessed().rin(n)
            result = seq if isinstance(seq, str) and seq else f"\033[{n}T"
        except Exception:
            result = f"\033[{n}T"
        self._stream.write(result)

    # ── 尺寸查询 ──────────────────────────────────────

    @property
    def height(self) -> int:
        """终端高度（实时查询）。"""
        try:
            return self._get_blessed().height
        except Exception:
            import shutil
            return shutil.get_terminal_size().lines

    @property
    def width(self) -> int:
        """终端宽度（实时查询）。"""
        try:
            return self._get_blessed().width
        except Exception:
            import shutil
            return shutil.get_terminal_size().columns

    # ── 锁上下文 ──────────────────────────────────────

    @contextmanager
    def locked(self, name: str = "terminal_io", timeout: float = 0.1):
        """获取输出锁的上下文管理器。

        未获取到锁时静默跳过 I/O 操作。
        """
        if self._lock is None:
            yield True
            return
        acquired = self._lock.acquire(timeout=timeout)
        try:
            yield acquired
        finally:
            if acquired:
                self._lock.release()

    # ── 内部 ──────────────────────────────────────────

    def _get_blessed(self):
        """惰性初始化 Blessed Terminal 单例。"""
        if self._blessed_term is None:
            try:
                from ..ui._blessed import get_terminal
                self._blessed_term = get_terminal()
                self._blessed_available = True
            except Exception:
                self._blessed_available = False
                raise
        return self._blessed_term

    @property
    def blessed_available(self) -> bool:
        """Blessed 是否可用。"""
        if self._blessed_available is None:
            try:
                self._get_blessed()
            except Exception:
                pass
        return self._blessed_available or False
