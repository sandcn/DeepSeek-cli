"""渲染引擎 — TuiEngine + render 线程 + 命令队列。

从 _tui.py 拆分，管理三阶段渲染流水线（预更新面板→获取输出锁→渲染命令→重绘底部栏）。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ._protocols import BottomBarProtocol, RenderEngine

from ._renderer import TuiRenderer

from ._const import (
    RenderCommand,
    _RENDER_INTERVAL,
    _DRAIN_LOCK_TIMEOUT,
    _ANSI_RED, _ANSI_RESET,
)

from ._utils import _cmd_name, _emergency_write

from ._lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)

# ── 引擎常量 ──────────────────────────────────────

_ACTIVE_RENDER_INTERVAL = 0.005
_CONSECUTIVE_FULL_THRESHOLD = 10


# ═══════════════════════════════════════════════════════════
# TuiEngine — 渲染引擎
# ═══════════════════════════════════════════════════════════

class TuiEngine:
    """渲染引擎 — render 线程 + Queue 命令队列 + 三阶段渲染循环。

    实现 RenderEngine 协议。
    组件化架构：所有内容通过 TuiRenderer 渲染，底部栏由 BottomBarProtocol 管理。
    """

    # 类级常量（从模块常量复制，允许测试通过实例属性覆盖）
    _ACTIVE_RENDER_INTERVAL = _ACTIVE_RENDER_INTERVAL
    _CONSECUTIVE_FULL_THRESHOLD = _CONSECUTIVE_FULL_THRESHOLD

    def __init__(
        self,
        renderer: "TuiRenderer",
        bottom_bar: "BottomBarProtocol",
        cursor_tracker: Any = None,
    ):
        self._renderer = renderer
        self._bb = bottom_bar
        self._cursor_tracker = cursor_tracker
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._cmd_event = threading.Event()
        self._render_thread: threading.Thread | None = None
        self._render_running = False
        self._consecutive_full = 0
        self._bottom_redraw_requested = threading.Event()
        self._panel_refresh_cb: Callable[[], None] | None = None
        self._cmd_queue_dropped: int = 0
        self._render_crashed: threading.Event = threading.Event()

    def push_cmd(self, cmd: tuple) -> None:
        """入队渲染命令到命令队列。

        非阻塞写入，队列满时丢弃并记录警告。
        连续满载超过阈值时升级为错误日志。

        Args:
            cmd: 渲染命令元组，格式为 (command_id, *args)
        """
        try:
            self._cmd_queue.put(cmd, block=False)
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            _logger.warning("渲染命令队列已满（%s 条），丢弃命令: %s", self._cmd_queue.qsize(), _cmd_name(cmd[0]))
            if self._consecutive_full >= self._CONSECUTIVE_FULL_THRESHOLD:
                _logger.error("渲染输出管线持续拥堵（%d 次连续满队列）", self._consecutive_full)
            if self._cmd_queue_dropped > 0 and self._cmd_queue_dropped % 100 == 0:
                try:
                    self._cmd_queue.put_nowait(
                        (RenderCommand.NOTIFICATION, f"渲染队列已丢弃 {self._cmd_queue_dropped} 条命令")
                    )
                except queue.Full:
                    pass

    @property
    def render_crashed(self) -> bool:
        """Render 线程是否已崩溃。"""
        return self._render_crashed.is_set()

    def set_panel_refresh_callback(self, callback: Callable[[], None] | None) -> None:
        self._panel_refresh_cb = callback

    def request_bottom_redraw(self) -> None:
        self._bottom_redraw_requested.set()

    def start(self) -> None:
        if self._render_thread is not None:
            if self._render_thread.is_alive():
                _logger.warning("start() 被重复调用，render 线程仍在运行，跳过")
                return
            self._render_thread.join()
        self._render_running = True
        self._render_thread = threading.Thread(target=self._render, daemon=True)
        self._render_thread.start()

    def stop(self) -> None:
        self._render_running = False
        if self._render_thread is not None:
            self._render_thread.join(timeout=2.0)
            if self._render_thread.is_alive():
                for _ in range(3):
                    self._render_thread.join(timeout=0.5)
                    if not self._render_thread.is_alive():
                        break
        self._drain_queue_safe()

    def flush(self, timeout: float | None = 5.0) -> None:
        if self._render_thread is None or not self._render_thread.is_alive():
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        task_done = threading.Thread(target=self._cmd_queue.join, daemon=True)
        task_done.start()
        task_done.join(timeout=timeout)

    def ensure_cursor_upper(self) -> None:
        self._bb.ensure_cursor_in_upper()

    # ── 三阶段流水线 ──────────────────────────────

    def _phase_pre_update_panels(self) -> None:
        """阶段 1：预更新面板回调。

        调用外部注册的面板刷新回调（如 SubAgent 面板帧更新），
        为空或异常均安全跳过。
        """
        if self._panel_refresh_cb is not None:
            try:
                self._panel_refresh_cb()
            except Exception:
                _logger.warning("panel_refresh_cb 异常", exc_info=True)

    def _phase_render(self, commands: list[tuple]) -> None:
        """阶段 2：执行渲染命令。

        同步底部栏行数后，遍历命令列表逐条分发给 TuiRenderer。
        单条命令失败时记录调试日志并入队错误提示，不中断循环。

        Args:
            commands: 一批待渲染的命令元组列表，每项格式为 (command_id, *args)
        """
        try:
            self._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        self.ensure_cursor_upper()
        for cmd in commands:
            try:
                self._renderer.render(cmd)
            except Exception:
                _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                self.push_cmd((RenderCommand.ERROR, f"渲染命令 {_cmd_name(cmd[0])} 失败"))

    def _phase_redraw_bottom(self, has_commands: bool) -> None:
        """阶段 3：重绘底部栏。

        在以下任一条件满足时触发强制重绘：
        - 本轮有渲染命令被处理
        - 外部请求了底部栏重绘（_bottom_redraw_requested）
        - 状态栏处于活跃状态

        Args:
            has_commands: 本轮 _drain_queue 是否处理了至少一条命令
        """
        redraw = has_commands or self._bottom_redraw_requested.is_set() or self._bb.is_status_active
        self._bottom_redraw_requested.clear()
        if redraw:
            try:
                self._bb.force_redraw()
            except Exception:
                _logger.debug("force_redraw 异常", exc_info=True)
            try:
                self._position_cursor()
            except Exception:
                _logger.debug("position_cursor 异常", exc_info=True)

    # ── render 线程 ────────────────────────────────

    def _render(self) -> None:
        """Render 线程主循环。

        在 daemon 线程中持续运行，循环执行三阶段流水线：
        drain_queue → 自适应等待 → 重复。异常时记录 critical 日志并终止循环。

        退出时（finally）安全排空命令队列。
        """
        idle_count = 0
        try:
            while self._render_running:
                try:
                    has_content = self._drain_queue()
                    if has_content:
                        idle_count = 0
                        wait_timeout = self._ACTIVE_RENDER_INTERVAL
                    else:
                        # 指数退避平滑过渡：5ms → 10ms → 20ms → 40ms → 80ms → 100ms
                        wait_timeout = min(
                            self._ACTIVE_RENDER_INTERVAL * (2 ** idle_count),
                            _RENDER_INTERVAL,
                        )
                        idle_count += 1
                    self._cmd_event.wait(timeout=wait_timeout)
                    if not has_content:
                        self._cmd_event.clear()
                except Exception:
                    self._render_crashed.set()
                    _logger.critical("render 线程异常崩溃", exc_info=True)
                    _emergency_write(
                        f"{_ANSI_RED}[ChatUI] render 线程异常终止，"
                        f"请联系开发人员查看日志{_ANSI_RESET}\n",
                        stream="stderr",
                    )
                    self._render_running = False
                    break
        finally:
            # 统计并报告丢弃的待处理命令
            dropped = 0
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                    dropped += 1
                except queue.Empty:
                    break
            if dropped > 0:
                _emergency_write(
                    f"{_ANSI_RED}[ChatUI] render 线程已终止，"
                    f"丢弃 {dropped} 条待处理命令{_ANSI_RESET}\n",
                    stream="stderr",
                )

    def _drain_queue(self) -> bool:
        """三阶段流水线：预处理面板→获取输出锁→渲染命令→重绘底部栏。

        阶段 1: _phase_pre_update_panels() — 刷新面板回调
        阶段 2: 获取输出锁，批量取出队列中所有命令
        阶段 3: _phase_render() 执行渲染命令，_phase_redraw_bottom() 重绘底部栏

        Returns:
            是否处理了至少一条渲染命令
        """
        commands: list[tuple] = []
        self._phase_pre_update_panels()
        with _try_acquire_output_lock(name="drain_queue", timeout=_DRAIN_LOCK_TIMEOUT) as locked:
            if not locked:
                return False
            while True:
                try:
                    commands.append(self._cmd_queue.get_nowait())
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            has_content = bool(commands)
            if commands:
                self._phase_render(commands)
            self._phase_redraw_bottom(has_content)
            return has_content

    def _drain_queue_safe(self) -> None:
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
            except queue.Empty:
                break
        if self._cmd_queue_dropped > 0:
            _logger.info("render 线程终止，共丢弃 %d 条命令", self._cmd_queue_dropped)

    def _position_cursor(self) -> None:
        if not self._bb.is_active:
            return
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        r_cursor, cursor_col = self._bb.compute_cursor_position(text, cursor_pos, h, w)
        try:
            from ..ui._blessed import get_terminal
            term = get_terminal()
            sys.__stdout__.write(term.move_xy(cursor_col - 1, r_cursor - 1))
        except Exception:
            _logger.debug("position_cursor Blessed 不可用, 使用 ANSI 回退", exc_info=True)
            sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()
        if self._cursor_tracker is not None:
            self._cursor_tracker.set(r_cursor, cursor_col)


# @deprecated — 使用 TuiEngine/TuiRenderer 替代，v1.3+ 将移除
RenderEngine = TuiEngine
ContentRenderer = TuiRenderer
