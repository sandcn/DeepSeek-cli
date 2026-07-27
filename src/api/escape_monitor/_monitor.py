"""EscapeMonitor 类 + 模块级导出函数。

核心监听逻辑、终端控制、中断分发。
仅负责原始 I/O（读字节 → 解析 → 推送到 Input 事件队列），
输入处理与分发统一由 Input.process_events() 在 render 线程中执行。
"""

from __future__ import annotations

import os
import sys
import time
import threading
import logging
from ._history import (
    MONITOR_JOIN_TIMEOUT,
    MONITOR_START_JOIN_TIMEOUT,
    UNIX_SELECT_TIMEOUT,
    WINDOWS_POLL_INTERVAL,
    _EOF_THRESHOLD,
    _SELECT_ERROR_THRESHOLD,
    _POLL_INTERVAL,
    INPUT_HISTORY_FILE,
    _active_monitor,
    _active_monitor_lock,
)
from ..interrupt_async import request_interrupt_async
from ...tui.input import KeyEvent

_logger = logging.getLogger(__name__)


class EscapeMonitor:
    """后台监听 Esc 键，用于中断所有任务。

    仅负责原始 I/O：读取字节 → 解析 → 推送到 Input 事件队列。
    Ctrl+G/O/N/R 等特殊按键在 I/O 线程中内联处理（暂停→回调→恢复→推送结果）。
    Ctrl+C / Esc 中断内联处理（快速路径，不入队列）。
    其余所有按键通过 Input.push_key_event/push_paste 入队，
    由 render 线程调用 Input.process_events() 统一分发。
    """

    def __init__(self, input_instance=None):
        if input_instance is not None:
            self._input = input_instance
        else:
            # fallback：无注入时自行创建 Input（向后兼容）
            from ...tui.input._input import Input
            self._input = Input(
                fd=sys.stdin.fileno(),
                history_file=INPUT_HISTORY_FILE,
            )

        self._lock = threading.RLock()
        self._interrupted = threading.Event()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._active.set()
        self._paused_ack = threading.Event()
        self._paused_ack.set()
        self._monitor_ready = threading.Event()
        self._monitor_ready.set()
        self._thread = None
        self._old_settings = None
        self._saved_original_settings = None

        # ── 故障检测计数器 ──
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason = None

    # ── 公开接口 ──────────────────────────────────────────

    def start(self, prefill: str = ""):
        """开始监听（非阻塞），在执行前调用。

        Args:
            prefill: 可选的预填文本。
        """
        global _active_monitor, _active_monitor_lock
        if self._thread is not None and self._thread.is_alive():
            _logger.warning(
                "检测到旧 monitor 线程仍在运行，等待其退出（最多 2.0s）"
            )
            self._thread.join(timeout=MONITOR_START_JOIN_TIMEOUT)
            if self._thread.is_alive():
                _logger.error(
                    "旧 monitor 线程 2s 后仍未退出，强制覆盖（极罕见情况）"
                )
            self._thread = None
        from ..interrupt_async import reset_interrupt_async
        reset_interrupt_async()
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        self._input.reset()
        self._input.load_history()
        if prefill:
            self._input.set_buffer(prefill)
        self._monitor_ready.clear()
        self._eof_count = 0
        self._select_error_count = 0
        self._exit_reason = None
        self._thread = threading.Thread(target=self._monitor, daemon=True)
        self._thread.start()
        self._monitor_ready.wait(timeout=1.0)
        if not self._thread.is_alive():
            self._monitor_ready.set()
            raise RuntimeError(
                f"EscapeMonitor 启动失败：线程未能正常启动 "
                f"(exit_reason={self._exit_reason})"
            )
        self._input.echo(self._input.get_current_text())
        with _active_monitor_lock:
            _active_monitor = self

    def stop(self):
        """停止监听，恢复终端设置。"""
        global _active_monitor, _active_monitor_lock
        self._stop.set()
        self._active.set()
        self._interrupted.clear()
        from ..interrupt_async import reset_interrupt_async
        reset_interrupt_async()
        if self._thread:
            self._thread.join(timeout=MONITOR_JOIN_TIMEOUT)
            if self._thread.is_alive():
                _logger.warning(
                    "EscapeMonitor 线程 join 超时（%.1fs），"
                    "保留引用等待线程自行退出，start() 将检测并等待",
                    MONITOR_JOIN_TIMEOUT,
                )
            else:
                self._thread = None
        self._restore_terminal_settings()
        with _active_monitor_lock:
            if _active_monitor is self:
                _active_monitor = None

    def resume(self):
        """恢复监听。"""
        self._interrupted.clear()
        from ..interrupt_async import reset_interrupt_async
        reset_interrupt_async()
        self._paused_ack.wait(timeout=1.0)
        self._paused_ack.clear()
        self._apply_monitor_settings()
        from src._compat_termios import termios
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except Exception:
            pass
        self._active.set()

    # ── 内部方法：终端控制 ────────────────────────────────

    def _pause_for_callback(self) -> None:
        """在 monitor 线程内部暂停监听（不等待 _paused_ack）。"""
        from ..interrupt_async import reset_interrupt_async
        self._interrupted.clear()
        reset_interrupt_async()
        self._active.clear()
        self._restore_terminal_settings()

    def _resume_from_callback(self) -> None:
        """在 monitor 线程内部恢复监听（不等待 _paused_ack）。"""
        from ..interrupt_async import reset_interrupt_async
        self._interrupted.clear()
        reset_interrupt_async()
        self._apply_monitor_settings()
        from src._compat_termios import termios
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except Exception:
            pass
        self._active.set()

    def _handle_special_key(self, action: str) -> None:
        """处理特殊按键（Ctrl+G/O/N/R）：暂停 monitor → 回调 → 恢复 → 推入队列。

        在 monitor 线程中调用。恢复终端 cooked mode 后回调可安全运行 vim 等交互式程序。
        """
        cb = self._input._special_key_callback
        if cb is None:
            return
        text = self._input.get_current_text()
        self._pause_for_callback()
        try:
            try:
                result = cb(action, text)
            except Exception:
                _logger.warning("特殊按键回调异常 (action=%s)", action, exc_info=True)
                return
        finally:
            self._resume_from_callback()
        if result is not None and result != text:
            self._input.push_buffer_replace(result)
        if action == 'editmsg':
            self._input.push_key_event(
                KeyEvent(kind='enter', char='\r', raw=b'\r', modifier=0)
            )

    def _do_interrupt(self):
        """设置本地和全局中断信号。使用 Input 委托方法操作缓冲区。"""
        if self._stop.is_set():
            return
        self._interrupted.set()
        if not self._input.has_queued_input():
            self._input.reset_and_echo()
        else:
            self._flush_stdin_residual()
        request_interrupt_async()

    def _monitor(self):
        """主监控循环，确保异常时恢复终端设置"""
        try:
            self._monitor_unix()
        except Exception as e:
            _logger.warning(
                "EscapeMonitor 线程异常退出: %s", e,
                exc_info=True,
            )
            self._restore_terminal_settings()
        else:
            if self._exit_reason is not None:
                _logger.warning(
                    "EscapeMonitor 线程因 %s 退出", self._exit_reason
                )

    def _restore_terminal_settings_impl(self):
        """实际终端设置恢复逻辑（无锁，由调用方保证线程安全）。"""
        from src._compat_termios import termios
        settings = self._old_settings
        if settings is None:
            settings = self._saved_original_settings
        if settings is not None:
            try:
                fd = sys.stdin.fileno()
                termios.tcsetattr(fd, termios.TCSADRAIN, settings)
                try:
                    termios.tcflush(fd, termios.TCIFLUSH)
                except Exception:
                    pass
                self._old_settings = None
            except Exception as e:
                _logger.warning("终端设置恢复失败: %s", e)

    def _restore_terminal_settings(self, *, _lock_held: bool = False):
        """确保终端设置恢复（在异常或线程结束时调用），线程安全。"""
        if _lock_held:
            self._restore_terminal_settings_impl()
        else:
            with self._lock:
                self._restore_terminal_settings_impl()

    def _wait_while_paused(self, timeout: float) -> bool:
        """等待直到活跃或停止，返回 True 表示应停止。"""
        self._paused_ack.set()
        while not self._stop.is_set():
            if self._active.wait(timeout=timeout):
                break
        return self._stop.is_set()

    def _apply_monitor_settings(self) -> None:
        """获取当前终端设置并设置为 cbreak 模式（线程安全）。"""
        from src._compat_termios import termios, tty
        with self._lock:
            try:
                fd = sys.stdin.fileno()
                self._old_settings = termios.tcgetattr(fd)
                tty.setcbreak(fd)
            except Exception as e:
                _logger.warning("设置终端 cbreak 模式失败: %s", e)

    def _flush_stdin_residual(self, max_flush: int = 50) -> None:
        """非阻塞清理 stdin 残留字节。"""
        import select
        flushed = 0
        while flushed < max_flush:
            if self._stop.is_set():
                return
            try:
                fd = sys.stdin.fileno()
                ready, _, _ = select.select([fd], [], [], 0.05)
                if not ready:
                    break
                os.read(fd, 1)
                flushed += 1
            except (ValueError, OSError, TypeError, AttributeError):
                break

    # ── 公开属性 ──────────────────────────────────────────

    @property
    def interrupted(self):
        return self._interrupted.is_set()

    @property
    def is_alive(self) -> bool:
        """EscapeMonitor 后台线程是否存活。"""
        return self._thread is not None and self._thread.is_alive()

    # ── monitor 主循环 ───────────────────────────────────

    def _monitor_unix(self):
        """Unix/Cygwin: 用 termios + select 读取原始按键。

        所有非中断按键推送至 Input 事件队列。
        中断（Ctrl+C / Esc）内联处理。
        特殊按键（Ctrl+G/O/N/R）内联处理（暂停→回调→恢复→推送结果）。
        """
        from src._compat_termios import HAS_TERMIOS, termios
        import select

        if not HAS_TERMIOS:
            self._monitor_ready.set()
            raise ImportError("termios 在当前平台（Windows）不可用，回退到 msvcrt 路径")

        fd = sys.stdin.fileno()
        try:
            self._old_settings = termios.tcgetattr(fd)
            self._saved_original_settings = self._old_settings
        except Exception as e:
            _logger.warning("无法获取终端设置，EscapeMonitor 不可用: %s", e)
            self._monitor_ready.set()
            return

        try:
            self._apply_monitor_settings()
            termios.tcflush(fd, termios.TCIFLUSH)
        finally:
            self._monitor_ready.set()

        try:
            while not self._stop.is_set():
                # ── 暂停状态处理 ──────────────────────────
                if not self._active.is_set():
                    self._restore_terminal_settings()
                    if self._wait_while_paused(UNIX_SELECT_TIMEOUT):
                        return
                    self._apply_monitor_settings()
                    self._flush_stdin_residual()
                    continue

                # ── 正常监听 ──────────────────────────────
                try:
                    ready, _, _ = select.select([fd], [], [], UNIX_SELECT_TIMEOUT)
                except (ValueError, OSError, TypeError, AttributeError):
                    self._select_error_count += 1
                    if self._select_error_count >= _SELECT_ERROR_THRESHOLD:
                        _logger.warning(
                            "select 错误连续 %d 次，判定 stdin 不可用，退出监听",
                            self._select_error_count,
                        )
                        self._exit_reason = "select_error"
                        return
                    time.sleep(UNIX_SELECT_TIMEOUT)
                    continue
                self._select_error_count = 0
                if not ready:
                    continue

                try:
                    raw = os.read(fd, 1)
                    if not raw:
                        self._eof_count += 1
                        if self._eof_count >= _EOF_THRESHOLD:
                            _logger.warning(
                                "stdin EOF 连续 %d 次，判定 pty 已断开，退出监听",
                                self._eof_count,
                            )
                            self._exit_reason = "eof"
                            return
                        time.sleep(UNIX_SELECT_TIMEOUT)
                        continue
                    self._eof_count = 0
                except (ValueError, OSError, TypeError):
                    continue

                first_byte = raw[0]

                # ── ASCII 控制字符分发 ────────────────────
                if first_byte < 0x20 or first_byte == 0x7F:
                    try:
                        event = self._input.feed_byte(first_byte)
                        if event is None:
                            # ESC (0x1b) → 需读取完整转义序列
                            self._handle_escape()
                        elif event.kind == "interrupt":
                            self._do_interrupt()
                            self._flush_stdin_residual()
                        elif event.kind == "ctrl_key":
                            # 特殊按键内联处理（需终端模式切换）
                            ch = event.char
                            if ch == '\x07':          # Ctrl+G → vim
                                self._handle_special_key('vim')
                            elif ch == '\x0f':        # Ctrl+O → /editmsg
                                self._handle_special_key('editmsg')
                            elif ch in ('\x0e', '\x12'):  # Ctrl+N/R → 切换模型
                                self._handle_special_key('switch_model')
                            else:
                                # 其他 Ctrl+key → 入队
                                self._input.push_key_event(event)
                        else:
                            # enter, tab, backspace, home, end, delete 等 → 入队
                            self._input.push_key_event(event)
                    except Exception:
                        _logger.warning("控制字符分发异常", exc_info=True)
                    continue

                # ── ASCII 可打印字符 ──
                if first_byte < 0x80:
                    try:
                        paste_text = self._input.try_read_paste(fd, chr(first_byte))
                        if len(paste_text) > 1:
                            self._input.push_paste(paste_text)
                        else:
                            event = self._input.feed_byte(first_byte)
                            if event is not None:
                                self._input.push_key_event(event)
                    except Exception:
                        _logger.warning("ASCII 可打印字符分发异常", exc_info=True)
                    continue

                # ── 多字节 UTF-8 序列 ──
                try:
                    ch = self._input.read_utf8_char(fd, first_byte)
                    if ch is not None:
                        paste_text = self._input.try_read_paste(fd, ch)
                        if len(paste_text) > 1:
                            self._input.push_paste(paste_text)
                        else:
                            self._input.push_key_event(
                                KeyEvent(kind='char', char=ch,
                                         raw=ch.encode("utf-8", errors="replace"))
                            )
                    else:
                        self._input.capture_bytes(bytes([first_byte]))
                except Exception:
                    _logger.warning("多字节 UTF-8 字符分发异常", exc_info=True)
        finally:
            self._restore_terminal_settings(_lock_held=False)

    def _handle_escape(self):
        """处理 Esc 按键 — 委托 InputParser 解析完整序列。

        中断（escape/interrupt）内联处理；其余推入 Input 事件队列。
        """
        fd = sys.stdin.fileno()
        event = self._input.parse_sequence(fd)
        kind = event.kind

        if kind in ("escape", "interrupt"):
            self._do_interrupt()
            self._flush_stdin_residual()
        elif kind == "arrow_up":
            self._input.push_key_event(event)
        elif kind == "arrow_down":
            self._input.push_key_event(event)
        elif kind == "arrow_right":
            self._input.push_key_event(event)
        elif kind == "arrow_left":
            self._input.push_key_event(event)
        elif kind == "home":
            self._input.push_key_event(event)
        elif kind == "end":
            self._input.push_key_event(event)
        elif kind == "delete":
            self._input.push_key_event(event)
        elif kind == "backspace":
            self._input.push_key_event(event)
        elif kind == "char":
            self._input.push_key_event(event)
        # kind == "unknown" / "csi_u" → 静默忽略

    def _monitor_win(self):
        """Windows (非 Cygwin): 用 msvcrt 读取按键。"""
        import msvcrt

        self._monitor_ready.set()

        try:
            while not self._stop.is_set():
                if not self._active.is_set():
                    if self._wait_while_paused(WINDOWS_POLL_INTERVAL):
                        return
                    while msvcrt.kbhit():
                        msvcrt.getch()
                    continue

                if not msvcrt.kbhit():
                    time.sleep(WINDOWS_POLL_INTERVAL)
                    continue

                ch = msvcrt.getch()
                if ch == b'\x1b':
                    self._handle_escape_win()
                elif ch == b'\x03':
                    self._do_interrupt()
                    while msvcrt.kbhit():
                        msvcrt.getch()
                elif ch == b'\x07':          # Ctrl+G
                    self._handle_special_key('vim')
                elif ch == b'\x0f':          # Ctrl+O
                    self._handle_special_key('editmsg')
                elif ch == b'\x0e':          # Ctrl+N
                    self._handle_special_key('switch_model')
                elif ch == b'\x12':          # Ctrl+R
                    self._handle_special_key('switch_model')
                elif ch == b'\x09':          # Tab
                    self._input.push_key_event(
                        KeyEvent(kind='tab', char='\t', raw=b'\t', modifier=0))
                elif ch in (b'\r', b'\n'):  # Enter
                    self._input.push_key_event(
                        KeyEvent(kind='enter', char='\r', raw=ch, modifier=0))
                elif ch in (b'\x08', b'\x7f'):  # Backspace
                    self._input.push_key_event(
                        KeyEvent(kind='backspace', char='\x7f', raw=ch, modifier=0))
                else:
                    try:
                        char = ch.decode("utf-8", errors="replace")
                        self._input.push_key_event(
                            KeyEvent(kind='char', char=char, raw=ch))
                    except Exception:
                        self._input.capture_bytes(ch)
        finally:
            self._restore_terminal_settings()

    def _handle_escape_win(self):
        """Windows 平台 Esc 序列处理。"""
        import msvcrt
        if not msvcrt.kbhit():
            self._do_interrupt()
            while msvcrt.kbhit():
                msvcrt.getch()
            return

        next_ch = msvcrt.getch()
        if next_ch == b'[':
            final_ch = None
            while msvcrt.kbhit():
                c = msvcrt.getch()
                if c.isalpha() or c == b'~':
                    final_ch = c
                    break
            if final_ch == b'A':
                self._input.push_key_event(
                    KeyEvent(kind='arrow_up', raw=b'\x1b[A'))
            elif final_ch == b'B':
                self._input.push_key_event(
                    KeyEvent(kind='arrow_down', raw=b'\x1b[B'))
            elif final_ch == b'C':
                self._input.push_key_event(
                    KeyEvent(kind='arrow_right', raw=b'\x1b[C'))
            elif final_ch == b'D':
                self._input.push_key_event(
                    KeyEvent(kind='arrow_left', raw=b'\x1b[D'))
        elif next_ch == b'O':
            if msvcrt.kbhit():
                msvcrt.getch()
        elif next_ch == b'\x1b':
            self._do_interrupt()
            while msvcrt.kbhit():
                msvcrt.getch()
        else:
            self._do_interrupt()


def get_active_monitor():
    """获取当前活跃的 EscapeMonitor 实例（如果有）。"""
    global _active_monitor_lock
    with _active_monitor_lock:
        return _active_monitor


def stop_active_monitor():
    """停止当前活跃的 EscapeMonitor（如果存在）。"""
    monitor = get_active_monitor()
    if monitor is not None:
        try:
            monitor.stop()
        except Exception:
            _logger.warning("EscapeMonitor.stop() 异常", exc_info=True)
