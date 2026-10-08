"""EscapeMonitor 类 + 模块级导出函数。

终端模式管理、中断分发。
stdin 读取已合并至 Render 线程（Input.read_stdin_once() 在渲染循环中每帧调用），
EscapeMonitor 仅负责终端 cbreak/cooked 模式切换和中断信号管理。

架构（单线程模型）：
  - Render 线程（daemon）：TuiEngine._drain_queue() 中每帧调用 Input.read_stdin_once()
  - EscapeMonitor: 终端模式管理 + 中断信号管理
  - Input.read_stdin_once(): 单次非阻塞 stdin 读取 + 直接分发
"""

from __future__ import annotations

import sys
import threading
import logging
from . import _registry
from ._registry import get_active_monitor, stop_active_monitor
from ..interrupt_async import reset_interrupt_async
from src._compat_termios import termios, termios_lock, tty

_logger = logging.getLogger(__name__)


class EscapeMonitor:
    """终端模式管理与中断分发。

    stdin 读取已合并至 Render 线程（Input.read_stdin_once() 在渲染循环中驱动）。
    EscapeMonitor 仅负责：
      - 终端 cbreak/cooked 模式切换
      - 中断信号管理（Ctrl+C / Esc）
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

        设置 cbreak 模式后激活 Input I/O（由 Render 线程通过 read_stdin_once() 驱动）。

        Args:
            prefill: 可选的预填文本。
        """
        self._started = True
        reset_interrupt_async(input_instance=self._input)
        self._interrupted.clear()
        self._stop.clear()
        self._active.set()
        self._input.reset()
        self._input.load_history()
        if prefill:
            self._input.set_buffer(prefill)
        # ★ 在首次 apply_monitor_settings() 前保存原始终端设置（与
        #   apply_monitor_settings 同持 ``termios_lock``——修复前两者的
        #   「读取 → 修改」序列可被其它持有者（如首帧 CPR 光标行查询）的
        #   「保存 → 临时 raw → 恢复」交错，导致 cbreak 被陈旧快照覆盖）。
        try:
            with termios_lock():
                self._saved_original_settings = termios.tcgetattr(sys.stdin.fileno())
        except Exception:
            pass
        self.apply_monitor_settings()
        self._input.start_io()
        self._input.echo(self._input.get_current_text())
        _registry.set_active_monitor(self)

    def stop(self):
        """停止监听，恢复终端设置。"""
        self._stop.set()
        self._active.set()
        self._interrupted.clear()
        reset_interrupt_async(input_instance=self._input)
        self._input.stop_io()
        self._restore_terminal_settings_impl()
        _registry.clear_active_monitor(self)

    def resume(self):
        """恢复监听。"""
        self._interrupted.clear()
        reset_interrupt_async(input_instance=self._input)
        self._paused_ack.wait(timeout=1.0)
        self._paused_ack.clear()
        self._paused_ack.set()
        self.apply_monitor_settings()
        self._input.resume_io()

    # ── 内部方法：终端控制 ────────────────────────────────

    def _restore_terminal_settings_impl(self):
        """实际终端设置恢复逻辑（无锁，由调用方保证线程安全）。"""
        settings = self._old_settings
        if settings is None:
            settings = self._saved_original_settings
        if settings is not None:
            try:
                fd = sys.stdin.fileno()
                # ★ termios 读-改-写序列互斥（见 src/_compat_termios 模块
                #   docstring）：恢复与其它持有者的临时修改串行化，避免
                #   交错覆盖。
                with termios_lock():
                    termios.tcsetattr(fd, termios.TCSADRAIN, settings)
                self._old_settings = None
            except Exception as e:
                _logger.warning("终端设置恢复失败: %s", e)

    def restore_terminal_settings(self) -> None:
        """确保终端设置恢复（在异常或停止时调用），线程安全。"""
        with self._lock:
            self._restore_terminal_settings_impl()

    def _restore_terminal_settings(self, *, _lock_held: bool = False):
        """[deprecated] 请使用 restore_terminal_settings()。"""
        if _lock_held:
            self._restore_terminal_settings_impl()
        else:
            self.restore_terminal_settings()

    def apply_monitor_settings(self) -> None:
        """获取当前终端设置并设置为 cbreak 模式（线程安全）。

        「读取原属性 → cbreak」的读-改-写序列持 ``termios_lock``——与
        其它 termios 持有者（首帧 CPR 光标行查询、RawModeController、kitty
        查询）串行化，避免交错恢复用陈旧快照覆盖 cbreak。
        """
        with self._lock:
            try:
                fd = sys.stdin.fileno()
                with termios_lock():
                    self._old_settings = termios.tcgetattr(fd)
                    tty.setcbreak(fd)
            except Exception as e:
                _logger.warning("设置终端 cbreak 模式失败: %s", e)

    def _apply_monitor_settings(self) -> None:
        """[deprecated] 请使用 apply_monitor_settings()。"""
        self.apply_monitor_settings()

    # ── 公开属性 ──────────────────────────────────────────

    @property
    def interrupted(self):
        """委托 Input.interrupted（Input.read_stdin_once() 中的 _do_interrupt 设置该标志）。"""
        return self._input.interrupted

    @property
    def is_alive(self) -> bool:
        """Input 的 I/O 是否处于激活状态（标志位管理，非线程存活检测）。"""
        return self._input.is_io_running

    def clear_interrupted(self) -> None:
        """清除中断标志。"""
        self._interrupted.clear()


# ── 模块级导出函数（re-export 自 ``_registry``，内核服务优先） ──
# ``get_active_monitor`` / ``stop_active_monitor`` 已上移至 ``._registry``
# （活跃实例单例真源 + 内核 ``ctx.escape_monitor`` 服务接入点），此处保持
# 既有 ``from ._monitor import ...`` 调用路径兼容。
