"""_StdoutLineTracker — transparent stdout line tracker.

Wraps sys.__stdout__ transparently, passing all writes through while tracking
complete lines (detected by \n) in a ring buffer.

机制：
  - 所有 write/flush 原封不动穿透到真实 stdout
  - 检测 \n 将内容按行拆分存入环形缓冲区（最大 300 行）
  - 使用统一正则按数据流顺序处理光标控制序列：
    \\033[{r};{c}H 绝对光标定位（r > scroll_end → 过滤底部栏内容）
    \\0338 / \\033[u 光标恢复 → 退出底部栏模式
"""

from __future__ import annotations

import re
from collections import deque
from typing import IO, Any
import threading
import time
import logging
import os
from pathlib import Path
from src.config.defaults import OUTPUT_HISTORY_FILE
from src.api.escape_monitor._history import (
    _lock_history_file,
    _unlock_history_file,
)
# ★ 方向1：输出历史 ANSI SGR 剥离复用 ink.helpers.strip_ansi（纯函数，
#   依赖仅 _screen/core.style/ink.output，无环）——历史文件/环形缓冲存纯文本。
from src.tui.ink.helpers import strip_ansi

# Unified regex matching cursor positioning (CUP) and cursor restore (DECRC/SCRC)
# sequences. Processed in data-stream order so that a restore between two
# cursor-positioning sequences takes effect at its actual position.
#
#   \033[{r};{c}H  — CUP (cursor absolute positioning)
#   \0338          — DECRC (restore cursor)
#   \033[u         — SCRC (restore cursor, ANSI.SYS variant)
_CONTROL_SEQ_RE = re.compile(
    r'\x1b\[(?P<row>\d+);(?P<col>\d+)H'  # CUP
    r'|\x1b8'                              # DECRC
    r'|\x1b\[u'                            # SCRC
)

_logger = logging.getLogger(__name__)

# BUG-T6：刷盘单飞 + 压缩冷却
#   - 刷盘线程在途（_flush_in_progress）时只置 pending 标志，不新建线程
#     （避免每 50 行无条件创建 daemon 线程 → 线程创建风暴 + 锁竞争）
#   - 输出历史压缩冷却：距上次成功刷盘不足该秒数时跳过压缩（防频繁压缩）
_COMPACT_COOLDOWN: float = 30.0


class _StdoutLineTracker:
    """Transparent stdout wrapper that tracks complete lines.

    All write/flush calls pass through to the real stdout unchanged.
    Lines are detected by \\n characters and stored in a ring buffer.
    Content written to the bottom bar area (detected via cursor positioning
    sequences) is filtered out and not tracked.
    """

    _MAX_LINES = 1000

    # flush timer 生命周期管理：
    #   _flush_timer_stop Event 用于防止 teardown() 后已触发的 callback
    #   创建新定时器（资源泄露）。停止流程：_stop_flush_timer() 先 set
    #   停止标志，再 cancel 当前 timer。_timer_flush_callback() 自重置前
    #   检查标志，已停止时不创建新定时器。
    #   若需要重启 timer（当前无重启场景），需先调用
    #   _flush_timer_stop.clear() 再调用 _start_flush_timer()。

    def __init__(self, real_stdout: IO[str]):
        self._real_stdout = real_stdout
        self._ring: deque[str] = deque(maxlen=self._MAX_LINES)
        self._partial_line: str = ""
        self._scroll_end: int = 0
        self._in_bottom_bar: bool = False
        self._output_buffer: list[str] = []
        self._buffer_lock = threading.Lock()
        self._last_flush_time: float = time.monotonic()
        # BUG-T6：刷盘单飞标志 + 压缩冷却时间戳（既有测试引用）
        self._flush_in_progress: bool = False
        self._last_compact_time: float = 0.0
        # 在途单飞刷盘线程引用（_flush_history 等待其完成，保证文件内容完整）
        self._flush_worker_thread: threading.Thread | None = None
        self._flush_timer: threading.Timer | None = None
        self._flush_timer_stop = threading.Event()
        self._output_history_file: Path = OUTPUT_HISTORY_FILE
        self._load_output_history()
        self._start_flush_timer()

    # ── File object protocol ──
    # # deprecated: 本模块作为 ``sys.__stdout__`` 替换方须兼容 File-object 协议
    # （encoding/errors/buffer/fileno/isatty/writable）——无生产调用方，协议
    # 兼容保留（不删除，供 ``sys.__stdout__`` 替换场景使用）。

    @property
    def encoding(self) -> str:
        return getattr(self._real_stdout, 'encoding', 'utf-8')

    @property
    def errors(self) -> str:
        return getattr(self._real_stdout, 'errors', 'strict')

    @property
    def buffer(self) -> Any:
        return self._real_stdout.buffer

    def fileno(self) -> int:
        return self._real_stdout.fileno()

    def isatty(self) -> bool:
        return self._real_stdout.isatty()

    def writable(self) -> bool:
        return True

    # ── Core write/flush ──

    def write(self, data: str) -> int:
        self._real_stdout.write(data)
        self._track(data)
        return len(data)

    def flush(self) -> None:
        self._real_stdout.flush()

    # ── Scroll end management ──

    def set_scroll_end(self, scroll_end: int) -> None:
        """Update the scroll region end row.

        Called by _BottomBar whenever DECSTBM is updated.
        scroll_end < 1 disables tracking.
        """
        self._scroll_end = scroll_end

    # ── Line tracking ──

    def track(self, data: str) -> None:
        """公开行跟踪入口 — 供 RenderOutput 内容写回调。

        语义与内部 ``_track`` 一致：按数据流顺序处理光标控制序列，
        检测完整行（\\n）存入环形缓冲与输出历史。
        """
        self._track(data)

    def _track(self, data: str) -> None:
        """Process data for line tracking.

        Uses a unified regex to match cursor positioning sequences
        (\\033[{r};{c}H) and cursor restore sequences (\\0338, \\033[u)
        in data-stream order.  Cursor positioning to a row > scroll_end
        enters bottom bar mode (content not tracked); cursor restore exits
        bottom bar mode.  All matched control sequences are stripped from
        tracked text.  Only tracks complete lines (ending with \\n) when
        scroll_end >= 1.
        """
        if self._scroll_end < 1:
            return

        prev_end = 0
        for m in _CONTROL_SEQ_RE.finditer(data):
            # Text before this control sequence
            if m.start() > prev_end:
                self._add_text(data[prev_end:m.start()])

            if m.group('row') is not None:
                # Cursor positioning: \033[{r};{c}H
                row = int(m.group('row'))
                was_in_bottom_bar = self._in_bottom_bar
                self._in_bottom_bar = (row > self._scroll_end)
                if self._in_bottom_bar != was_in_bottom_bar:
                    self._partial_line = ""
            else:
                # Cursor restore: \0338 or \033[u → exit bottom bar mode
                if self._in_bottom_bar:
                    self._in_bottom_bar = False
                    self._partial_line = ""

            prev_end = m.end()

        # Remaining text after last control sequence
        if prev_end < len(data):
            self._add_text(data[prev_end:])

    def _add_text(self, text: str) -> None:
        """Accumulate text and extract complete lines (split on \\n).

        方向1（ANSI SGR 剥离）：完整行在存入环形缓冲/输出历史前剥离 ANSI
        转义序列（复用 ink.helpers.strip_ansi）——输出历史是用户可读记录，
        不应含 SGR 颜色序列；环形缓冲/历史文件均存纯文本。
        """
        self._partial_line += text
        if '\n' in self._partial_line:
            *complete_lines, self._partial_line = self._partial_line.split('\n')
            if not self._in_bottom_bar:
                for line in complete_lines:
                    plain_line = strip_ansi(line)
                    self._ring.append(plain_line)
                    self._buffer_to_output(plain_line)

    def _buffer_to_output(self, line: str) -> None:
        """将完整行加入输出历史缓冲，达到阈值时异步刷盘（单飞）。

        BUG-T6 单飞（single-flight）：刷盘线程在途时只置 pending 标志，
        不新建线程（避免每 50 行无条件创建 daemon 线程 → 线程创建风暴 +
        锁竞争）。线程完成刷盘后检查残留（在途期间新积累的行），>=50 则
        再次启动，保证不丢行。
        """
        with self._buffer_lock:
            self._output_buffer.append(line)
            if len(self._output_buffer) >= 50 and not self._flush_in_progress:
                self._flush_in_progress = True
                thread = threading.Thread(target=self._flush_worker, daemon=True)
                self._flush_worker_thread = thread
                thread.start()

    def _flush_worker(self) -> None:
        """刷盘工作线程：执行刷盘后复位单飞标志并检查残留。"""
        try:
            self._flush_buffered_lines()
        finally:
            with self._buffer_lock:
                self._flush_in_progress = False
                if len(self._output_buffer) >= 50:
                    # 在途期间新积累的行达到阈值 → 再次启动（不丢行）
                    self._flush_in_progress = True
                    thread = threading.Thread(target=self._flush_worker, daemon=True)
                    self._flush_worker_thread = thread
                    thread.start()
                else:
                    self._flush_worker_thread = None

    def _flush_buffered_lines(self) -> bool:
        """刷出输出缓冲中的行到历史文件。"""
        try:
            with self._buffer_lock:
                buf = self._output_buffer
                self._output_buffer = []
            if not buf:
                return True
            try:
                self._output_history_file.parent.mkdir(parents=True, exist_ok=True)
                with open(self._output_history_file, "a", encoding="utf-8") as f:
                    locked = _lock_history_file(f.fileno(), shared=False)
                    if not locked:
                        return False
                    try:
                        for line in buf:
                            f.write(line + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                    finally:
                        _unlock_history_file(f.fileno())
            except OSError as exc:
                _logger.warning("输出历史刷盘失败: %s", exc)
                return False
            self._last_flush_time = time.monotonic()
            # BUG-T6：成功刷盘后更新压缩冷却时间戳（供 _maybe_compact_output_history 判定）
            self._last_compact_time = time.monotonic()
            try:
                self._maybe_compact_output_history()
            except Exception:
                pass
            return True
        except Exception:
            return False

    def _load_output_history(self) -> None:
        """从输出历史文件加载最后 N 行到环形缓冲。"""
        try:
            path = self._output_history_file
            if not path.exists():
                return

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                locked = _lock_history_file(f.fileno(), shared=True)
                if not locked:
                    return
                try:
                    content = f.read()
                finally:
                    _unlock_history_file(f.fileno())

            if not content:
                return

            lines = content.splitlines()
            restore = lines[-self._MAX_LINES:] if len(lines) > self._MAX_LINES else lines
            for line in restore:
                # 方向1：历史文件可能含旧 SGR 残留——加载时同样剥离
                self._ring.append(strip_ansi(line))

        except (OSError, FileNotFoundError):
            _logger.debug("输出历史文件不存在，跳过加载")

    def _flush_history(self) -> None:
        """停止定时器并刷出所有剩余输出行到历史文件。

        BUG-T6 单飞后：在途 worker 可能持有大块缓冲（含尚未落盘的行）。
        本方法循环「等待在途 worker 完成 → 排空缓冲」，直到缓冲为空且无
        在途 worker，确保最终刷盘后文件内容完整且行序正确。
        """
        self._stop_flush_timer()
        for _ in range(2000):
            with self._buffer_lock:
                pending = len(self._output_buffer)
                in_flight = self._flush_in_progress
            if pending == 0 and not in_flight:
                break
            thread = self._flush_worker_thread
            if thread is not None and thread.is_alive():
                # 先等待在途 worker 完成（保证文件行序：先写入其持有的块）
                thread.join(timeout=0.01)
                continue
            try:
                self._flush_buffered_lines()
            except Exception:
                _logger.warning("_flush_history: 最终刷盘异常", exc_info=True)
                break

    def _start_flush_timer(self) -> None:
        """启动 2 秒定时刷盘定时器。"""
        if self._flush_timer_stop.is_set():
            return
        timer = threading.Timer(2.0, self._timer_flush_callback)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _timer_flush_callback(self) -> None:
        """定时刷盘回调，自重置定时器。"""
        try:
            self._flush_buffered_lines()
        except Exception:
            pass
        if self._flush_timer is not None and not self._flush_timer_stop.is_set():
            self._start_flush_timer()

    def _stop_flush_timer(self) -> None:
        """停止定时刷盘定时器。"""
        self._flush_timer_stop.set()
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _maybe_compact_output_history(self) -> bool:
        """检查并压缩输出历史文件（>5000行时去重+截断至2000行）。

        BUG-T6 压缩冷却：距上次成功刷盘不足 _COMPACT_COOLDOWN 秒时跳过，
        避免频繁压缩（每 50 行刷盘都会触发检查）。
        """
        if time.monotonic() - self._last_compact_time < _COMPACT_COOLDOWN:
            return False
        try:
            path = self._output_history_file
            if not path.exists():
                return False

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                locked = _lock_history_file(f.fileno(), shared=False)
                if not locked:
                    return False
                try:
                    content = f.read()
                finally:
                    _unlock_history_file(f.fileno())

            if not content:
                return False

            lines = content.splitlines()
            if len(lines) <= 5000:
                return False

            # 去重（保留首次出现的行）+ 取最后2000行
            seen: set[str] = set()
            unique: list[str] = []
            for line in lines:
                if line not in seen:
                    unique.append(line)
                    seen.add(line)

            keep = unique[-2000:] if len(unique) > 2000 else unique

            # 原子写入
            tmp_path = path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as tmp:
                for line in keep:
                    tmp.write(line + "\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            os.rename(tmp_path, path)
            _logger.debug("输出历史压缩完成: %d行→去重%d行→保留%d行", len(lines), len(unique), len(keep))
            return True

        except (OSError, FileNotFoundError) as exc:
            _logger.warning("输出历史压缩失败: %s", exc)
            try:
                tmp_path = self._output_history_file.with_suffix(".tmp")
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False
