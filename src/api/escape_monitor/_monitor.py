"""EscapeMonitor 类 + 模块级导出函数。

终端模式管理、中断分发。
I/O 线程已迁移至 Input 内部（Input._io_loop daemon 线程），
EscapeMonitor 仅负责终端 cbreak/cooked 模式切换和中断信号管理。

架构（统一输入重构后）：
  - Input._io_loop: daemon 线程，从 stdin fd 读取原始字节并解析入队
  - EscapeMonitor: 终端模式管理 + 中断信号 + 特殊按键终端切换
  - Input.process_events(): render 线程统一分发事件
"""

from __future__ import annotations

import os
import sys
import threading
import logging
from ._history import (
    _active_monitor,
    _active_monitor_lock,
)
from ..interrupt_async import request_interrupt_async

_logger = logging.getLogger(__name__)


class EscapeMonitor:
    """终端模式管理与中断分发。

    I/O 线程已迁移至 Input 内部（Input._io_loop daemon 线程）。
    EscapeMonitor 仅负责：
      - 终端 cbreak/cooked 模式切换
      - 中断信号管理（Ctrl+C / Esc）
      - 特殊按键（Ctrl+G/O/N/R）时的终端模式切换
    """

    def __init__(self, input_instance=None):
        if input_instance is None:
            raise ValueError(
                "EscapeMonitor 需要有效的 Input 实例。"
                "在统一输入架构中，Input 实例由工厂创建后通过 input_instance 参数注入。"
            )
        self._input = input_instance

        self._lock = threading.RLock()
        self._interrupted = threading.Event()
        self._stop = threading.Event()
        self._active = threading.Event()
        self._active.set()
        self._paused_ack = threading.Event()
        self._paused_ack.set()
        self._old_settings = None
        self._saved_original_settings = None
        self._started = False

    # ── 公开接口 ──────────────────────────────────────────

    def start(self, prefill: str = ""):
        """开始监听（非阻塞），在执行前调用。

        设置 cbreak 模式后启动 Input 内部 daemon I/O 线程。

        Args:
            prefill: 可选的预填文本。
        """
        global _active_monitor, _active_monitor_lock
        self._started = True
        from ..interrupt_async import reset_interrupt_async
        reset_interrupt_async()
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        self._input.reset()
        self._input.load_history()
        if prefill:
            self._input.set_buffer(prefill)
        self._apply_monitor_settings()
        self._input.start_io()
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
        self._input.stop_io()
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
        self._input.resume_io()

    # ── 内部方法：终端控制 ────────────────────────────────

    def _pause_for_callback(self) -> None:
        """在 I/O 线程内部暂停监听：暂停 Input I/O + 恢复终端 cooked 模式。

        用于特殊按键回调（Ctrl+G/O/N/R），使 vim 等交互程序可正常运行。
        """
        from ..interrupt_async import reset_interrupt_async
        self._interrupted.clear()
        reset_interrupt_async()
        self._input.pause_io()
        self._restore_terminal_settings()

    def _resume_from_callback(self) -> None:
        """在 I/O 线程内部恢复监听：恢复 cbreak 模式 + 恢复 Input I/O。"""
        from ..interrupt_async import reset_interrupt_async
        self._interrupted.clear()
        reset_interrupt_async()
        self._apply_monitor_settings()
        from src._compat_termios import termios
        try:
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
        except Exception:
            pass
        self._input.resume_io()

    def _handle_special_key(self, action: str, text: str) -> str | None:
        """特殊按键回调 — 终端模式切换 + 调用真实回调。

        被设置为 Input._special_key_callback，由 Input._handle_special_key 调用。
        Input 负责 I/O 暂停/恢复和结果推送，此方法负责终端模式切换。

        Args:
            action: 特殊按键动作标识（'vim' | 'editmsg' | 'switch_model'）。
            text: 当前输入文本。

        Returns:
            修改后的文本，或 None 表示不修改。
        """
        cb = self._input._special_key_callback
        if cb is None or cb is self._handle_special_key:
            return text
        self._pause_for_callback()
        try:
            try:
                result = cb(action, text)
            except Exception:
                _logger.warning("特殊按键回调异常 (action=%s)", action, exc_info=True)
                return text
        finally:
            self._resume_from_callback()
        return result if result is not None else text

    def _do_interrupt(self):
        """设置中断信号（外部中断请求）。

        委托 Input 处理缓冲区清空和中断标志设置。
        Input._io_loop 中的 Ctrl+C 中断由 Input._do_interrupt() 内联处理（快速路径）。
        此方法供外部调用（如程序化中断）。
        """
        if self._stop.is_set():
            return
        self._interrupted.set()
        if not self._input.has_queued_input():
            self._input.reset_and_echo()
        else:
            self._input._flush_stdin_residual()
        self._input._interrupted.set()
        request_interrupt_async()

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

    # ── 公开属性 ──────────────────────────────────────────

    @property
    def interrupted(self):
        """委托 Input.interrupted（Input 的 _io_loop 中的 _do_interrupt 设置该标志）。"""
        return self._input.interrupted

    @property
    def is_alive(self) -> bool:
        """Input 的 I/O 线程是否存活。"""
        return self._input.is_io_running


# ── 模块级导出函数 ──────────────────────────────────────────


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
