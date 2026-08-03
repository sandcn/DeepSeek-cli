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
# 方向1 步骤2（ANSI 单一工具）：光标控制序列解析复用统一
# ``ink.helpers.cursor_control_re``（CUP/DECRC/SCRC 分组语义迁移自本文件
# 旧 ``_CONTROL_SEQ_RE``；本文件不再定义独立正则）。
from src.tui.ink.helpers import strip_ansi, cursor_control_re

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
        # ★ 方向1（追加写与压缩 rename 串行化）：文件级操作锁（进程内互斥）——
        #   刷盘追加写（_flush_buffered_lines）与输出历史压缩（_maybe_compact_
        #   output_history）并发时，压缩的 os.rename 会把追加写持有的旧 inode
        #   换掉，追加行落入已删除文件 → 行丢失。本锁保证两段操作互斥；
        #   跨进程安全仍由 _lock_history_file（flock）保证。
        self._file_io_lock = threading.Lock()
        self._last_flush_time: float = time.monotonic()
        # BUG-T6：刷盘单飞标志 + 压缩冷却时间戳（既有测试引用）
        self._flush_in_progress: bool = False
        self._last_compact_time: float = 0.0
        # ★ BUG-20（review 方向）：定时刷盘在 worker 在途时置位待刷标志——
        #   worker finally 处理（防止 timer 与 worker 并发写文件行序颠倒）。
        self._pending_flush: bool = False
        # 在途单飞刷盘线程引用（_flush_history 等待其完成，保证文件内容完整）
        self._flush_worker_thread: threading.Thread | None = None
        self._flush_timer: threading.Timer | None = None
        self._flush_timer_stop = threading.Event()
        self._output_history_file: Path = OUTPUT_HISTORY_FILE
        self._load_output_history()
        self._start_flush_timer()
        # 方向2（close 幂等标志）：close() 后停止一切刷盘活动（防重复执行）
        self._closed: bool = False

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

        Uses the unified cursor control regex (``ink.helpers.cursor_control_re``)
        to match cursor positioning sequences (\\033[{r};{c}H) and cursor
        restore sequences (\\0338, \\033[u) in data-stream order.  Cursor
        positioning to a row > scroll_end enters bottom bar mode (content not
        tracked); cursor restore exits bottom bar mode.  All matched control
        sequences are stripped from tracked text.  Only tracks complete lines
        (ending with \\n) when scroll_end >= 1.
        """
        if self._scroll_end < 1:
            return

        prev_end = 0
        for m in cursor_control_re.finditer(data):
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

        方向1 步骤2（CRLF 修复）：完整行在 split 后对每行 ``rstrip("\\r")``
        ——CRLF 终端行尾残留 \\r 剥除（行存入环形缓冲/输出历史前）。行中段
        \\r 不剥（仅行尾）；先剥 ANSI 再 rstrip \\r（顺序固定，与 1.1 的
        ANSI 剥离协同）。
        """
        self._partial_line += text
        if '\n' in self._partial_line:
            *complete_lines, self._partial_line = self._partial_line.split('\n')
            if not self._in_bottom_bar:
                for line in complete_lines:
                    plain_line = strip_ansi(line).rstrip("\r")
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
        """刷盘工作线程：执行刷盘后复位单飞标志并检查残留。

        ★ BUG-20：复位后若 ``_pending_flush`` 置位（定时器在 worker 在途时
        积累的行）且缓冲非空 → 继续刷盘（与阈值无关）；修复前仅 ``>=50``
        重启——timer 与 worker 并发写文件可能行序颠倒（后到先写）。
        """
        try:
            self._flush_buffered_lines()
        finally:
            with self._buffer_lock:
                self._flush_in_progress = False
                if len(self._output_buffer) >= 50 or (
                    self._pending_flush and self._output_buffer
                ):
                    # 在途期间新积累的行达到阈值 / 定时器待刷 → 再次启动
                    if self._pending_flush and len(self._output_buffer) < 50:
                        self._pending_flush = False
                    self._flush_in_progress = True
                    thread = threading.Thread(target=self._flush_worker, daemon=True)
                    self._flush_worker_thread = thread
                    thread.start()
                else:
                    self._flush_worker_thread = None
                    self._pending_flush = False

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
                # ★ 方向1：追加写持 _file_io_lock——与压缩（rename）互斥，
                #   防止并发 rename 期间追加写入落旧 inode 丢失行。
                with self._file_io_lock:
                    with open(self._output_history_file, "a", encoding="utf-8") as f:
                        locked = _lock_history_file(f.fileno(), shared=False)
                        if not locked:
                            # ★ BUG-19（review 方向）：加锁失败时**行放回缓冲**
                            #   ——修复前 ``buf`` 已从 ``_output_buffer`` 移除但未
                            #   写盘，直接 return False → 这些行永久丢失（跨进程
                            #   flock 冲突时输出历史记录缺失）。放回头部后由
                            #   后续刷盘（定时器/worker finally/close）重试。
                            with self._buffer_lock:
                                self._output_buffer = buf + self._output_buffer
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
                # ★ BUG-19：OSError（文件系统瞬时错误）同样放回缓冲防丢行
                with self._buffer_lock:
                    self._output_buffer = buf + self._output_buffer
                return False
            self._last_flush_time = time.monotonic()
            # 方向2（压缩冷却修复）：**不再**更新 ``_last_compact_time``——
            # 该字段语义为「上次压缩执行时间」，由 ``_maybe_compact_output_history``
            # 自行更新（修复前每次刷盘后更新 → now-last<30 恒成立 → 压缩永不触发）。
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

        ★ BUG-29（review 方向）：循环上限（2000 次 × join 0.01s ≈ 20s）后
        追加一次**无条件最终刷盘**——修复前若 worker 被慢盘挂起超过上限，
        退出时 ``_output_buffer`` 残留行不再刷盘 → 进程退出后历史行丢失。
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
        else:
            # 循环自然耗尽（20s 上限）：残留行兜底刷盘（尽力而为，不丢行）
            try:
                self._flush_buffered_lines()
            except Exception:
                _logger.warning("_flush_history: 兜底刷盘异常", exc_info=True)

    def close(self) -> None:
        """停止定时刷盘并刷出所有剩余输出行到历史文件（幂等）。

        方向2（_flush_history 接线修复）：``close()`` 为生命周期关闭入口——
        ``TuiLifecycle.stop`` 停止流程调用（经 ``session._line_tracker``）：
        停止 daemon 刷盘定时器（不再自重置泄漏）+ 循环排空缓冲直至空且无
        在途 worker（复用 ``_flush_history`` 逻辑），输出历史文件含全部缓冲行。
        幂等：``_closed`` 标志防重复执行；重复调用安全返回。
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._flush_history()
        except Exception:
            _logger.warning("close: 最终刷盘异常", exc_info=True)

    def _start_flush_timer(self) -> None:
        """启动 2 秒定时刷盘定时器。"""
        if self._flush_timer_stop.is_set():
            return
        timer = threading.Timer(2.0, self._timer_flush_callback)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _timer_flush_callback(self) -> None:
        """定时刷盘回调，自重置定时器。

        ★ BUG-20：worker 在途（``_flush_in_progress``）时仅置
        ``_pending_flush``（由 worker finally 统一处理）——修复前直接调用
        ``_flush_buffered_lines()`` 与 worker 并发写文件，两个线程各自取不同
        批次，写盘顺序取决于锁竞争（后到行可能先写）→ 输出历史乱序。
        """
        try:
            if self._flush_in_progress:
                self._pending_flush = True
            else:
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

        方向2（压缩冷却修复）：``_last_compact_time`` 语义为「上次压缩执行
        时间」——通过冷却检查即更新（成功/尝试后）。修复前 ``_flush_buffered_lines``
        每次刷盘后更新该字段 → now-last<30 恒成立 → 压缩永不触发。

        方向1（并发压缩修复）：整段操作（冷却检查 + 读 + 写 tmp + rename）持
        ``_file_io_lock``——与 ``_flush_buffered_lines`` 的追加写串行化（防止
        rename 期间追加写落旧 inode 丢行）；冷却检查同锁内读，双线程不会同时
        通过检查并发压缩（第二次压缩在锁内重读文件行数已 <=5000 自然跳过）。
        """
        with self._file_io_lock:
            if time.monotonic() - self._last_compact_time < _COMPACT_COOLDOWN:
                return False
            # 通过冷却检查 → 记录本次压缩尝试（防频繁检查）
            self._last_compact_time = time.monotonic()
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
