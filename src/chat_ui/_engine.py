"""RenderEngine — Reader 线程 + 命令队列 + 渲染循环。

独立管理线程生命周期（start/stop/suspend/resume）、
命令队列消费与终端光标定位。与 EventDispatcher / ContentRenderer
通过组合协作。
"""

from __future__ import annotations

import logging
import queue
import shutil
import sys
import threading
import time
from typing import TYPE_CHECKING

from ..ui._bottom_bar import _BottomBar, _compute_cursor_visual_pos
from ..ui._lock import _try_acquire_output_lock, output_lock
from ._const import _READER_INTERVAL, RenderCommand, _cmd_name
from ._renderers import ContentRenderer

if TYPE_CHECKING:
    from ..ui.parallel.display import ParallelDisplay
    from ._state import _active_parallel_display as _pd_module

_logger = logging.getLogger(__name__)


class RenderEngine:
    """Reader 线程 + 命令队列 + 渲染循环。

    以 10Hz 轮询命令队列，串行执行渲染（通过 output_lock 串行化 I/O）。
    支持 start/stop/suspend/resume 四种生命周期状态。
    """

    def __init__(
        self,
        cmd_queue: queue.Queue,
        renderer: ContentRenderer,
        bottom_bar: _BottomBar,
        get_active_pd: callable,
    ):
        self._cmd_queue = cmd_queue
        self._renderer = renderer
        self._bottom_bar = bottom_bar
        self._get_active_pd = get_active_pd  # 返回 _active_parallel_display 的函数

        # Reader 线程
        self._reader_thread: threading.Thread | None = None
        self._reader_running = False
        self._cmd_event = threading.Event()

    # ── 生命周期 ─────────────────────────────────────

    def start(self) -> None:
        """启动 reader 线程。幂等。"""
        if self._reader_running:
            return
        self._reader_running = True
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        """停止 reader 线程。幂等。"""
        if not self._reader_running:
            return
        self._reader_running = False
        self._cmd_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            if not self._reader_thread.is_alive():
                self._reader_thread = None

    def suspend(self) -> None:
        """暂停 reader 线程 + 清空队列。幂等。"""
        if self._reader_running:
            self._reader_running = False
            self._cmd_event.set()
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=2.0)
                if not self._reader_thread.is_alive():
                    self._reader_thread = None
        self._drain_nowait()
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.1)
            if not self._reader_thread.is_alive():
                self._reader_thread = None

    def resume(self) -> None:
        """恢复 reader 线程。仅在已 start() 但 reader 已停止时有效。"""
        with output_lock:
            height = shutil.get_terminal_size().lines
            sys.__stdout__.write(f"\033[{height};1H")
            sys.__stdout__.flush()
            # 无条件恢复运行标记；join 超时后旧线程虽未结束，
            # _reader_running=True 使其继续运行
            self._reader_running = True
            if self._reader_thread is None or not self._reader_thread.is_alive():
                self._reader_thread = threading.Thread(target=self._reader, daemon=True)
                self._reader_thread.start()

    # ── Reader 线程循环 ──────────────────────────────

    def _reader(self) -> None:
        """Reader 线程入口，10Hz 轮询消费。"""
        while self._reader_running:
            self._drain_queue()
            self._cmd_event.wait(timeout=_READER_INTERVAL)
            self._cmd_event.clear()

    def _drain_queue(self) -> None:
        """消费所有待处理渲染命令，执行上屏渲染 + 底部栏重绘。

        四阶段流水线：
          0. 尺寸检测（1s 超时）
          1. 上屏渲染（锁内出队+渲染）
          2. ParallelDisplay 面板刷新（无锁）
          3. 底部栏重绘 + 光标定位
        """
        # ★ 阶段 0：尺寸检测
        with _try_acquire_output_lock(name="drain_queue.resize", timeout=1.0) as locked:
            resized = locked and self._bottom_bar.check_resize()

        # ★ 阶段 1：锁内批量出队 + 上屏渲染
        commands: list[tuple] = []
        with _try_acquire_output_lock(name="drain_queue.render", timeout=1.0) as locked:
            if locked:
                while True:
                    try:
                        commands.append(self._cmd_queue.get_nowait())
                        self._cmd_queue.task_done()
                    except queue.Empty:
                        break
                if commands:
                    self._bottom_bar.ensure_cursor_in_upper()
                    for cmd in commands:
                        try:
                            self._renderer.render(cmd)
                        except Exception:
                            _logger.debug(
                                "drain_queue: 渲染命令 %s 失败", cmd,
                                exc_info=True,
                            )
                            self._renderer._push_error(
                                f"渲染命令 {_cmd_name(cmd[0])} 失败，请查看日志获取详情",
                            )
                    sys.__stdout__.flush()

        # ★ 阶段 2：ParallelDisplay 面板刷新（无锁）
        pd = self._get_active_pd()
        if pd is not None:
            try:
                pd.refresh()
            except Exception:
                _logger.debug(
                    "drain_queue: ParallelDisplay 刷新异常", exc_info=True,
                )
                self._renderer._push_error(
                    "drain_queue: ParallelDisplay 刷新失败，请查看日志获取详情",
                )

        # ★ 阶段 3：底部栏重绘 + 光标定位
        if commands or resized:
            with _try_acquire_output_lock(name="drain_queue.bottom", timeout=1.0) as locked:
                if locked:
                    self._bottom_bar.force_redraw()
                    self._position_cursor()
        elif self._bottom_bar.is_status_active:
            self._bottom_bar.refresh_status_only()
            self._position_cursor()

    def _drain_nowait(self) -> None:
        """非阻塞清空命令队列。"""
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
            except queue.Empty:
                break

    # ── 光标定位 ─────────────────────────────────────

    def _position_cursor(self) -> None:
        """光标移回输入行，根据超长文本自动拆行定位（含最少3行输入区）。"""
        text, cursor_pos, h, w = self._bottom_bar.get_cursor_info()
        max_input = max(1, w - 4)

        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total_bottom = self._bottom_bar._bottom_lines
        r_cursor = max(1, h - total_bottom + 3 + vis_row)
        cursor_col = min(3 + vis_col, w)
        sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()

    # ── Flush ─────────────────────────────────────────

    def flush(self, timeout: float = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。

        Reader 未运行时直接清空队列，Reader 运行时通过 queue.join() 等待。
        """
        self._cmd_event.set()
        if self._reader_thread is None:
            self._drain_nowait()
            return
        task_done = threading.Thread(
            target=self._cmd_queue.join, daemon=True,
        )
        task_done.start()
        task_done.join(timeout=timeout)
