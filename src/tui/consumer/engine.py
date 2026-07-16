"""渲染引擎 — TuiEngine + render 线程 + 命令队列。

从 _tui.py 拆分，管理三阶段渲染流水线（预更新面板→获取输出锁→渲染命令→重绘底部栏）。

【inline 模式 · 2026-07-16 重构】
移除 DECSTBM 相关调用（sync_bottom_lines / ensure_cursor_upper / position_cursor），
底部栏 inline 模式下 force_redraw() 自行处理全部光标定位。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .protocols import BottomBarProtocol, RenderEngine

from .renderer import TuiRenderer

from .const import (
    RenderCommand,
    _RENDER_INTERVAL,
    _DRAIN_LOCK_TIMEOUT,
    _ANSI_RED, _ANSI_RESET,
    _MAX_BATCH_SIZE,
)

from .utils import _cmd_name, _emergency_write

from .lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)

# ── 引擎常量 ──────────────────────────────────────

_ACTIVE_RENDER_INTERVAL = 0.1
_CONSECUTIVE_FULL_THRESHOLD = 10


# ═══════════════════════════════════════════════════════════
# TuiEngine — 渲染引擎（inline 模式）
# ═══════════════════════════════════════════════════════════

class TuiEngine:
    """渲染引擎 — render 线程 + Queue 命令队列 + 三阶段渲染循环。

    实现 RenderEngine 协议。
    组件化架构：所有内容通过 TuiRenderer 渲染，底部栏由 BottomBarProtocol 管理。

    inline 模式（2026-07-16）：移除 DECSTBM 依赖，底部栏 force_redraw()
    自行处理全部光标定位，引擎不再调用 sync_bottom_lines / ensure_cursor_upper。
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
        # ── 10Hz 底部栏重绘定时器 ──
        self._last_bottom_redraw: float = 0.0
        self._BOTTOM_REDRAW_INTERVAL: float = 0.1  # 10Hz 底部栏重绘间隔

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
        self._cmd_event.set()

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
        task_done = threading.Thread(target=self._cmd_queue.join, daemon=False)
        task_done.start()
        task_done.join(timeout=timeout)
        if task_done.is_alive():
            self._drain_queue_safe()
            task_done.join(timeout=1.0)

    def ensure_cursor_upper(self) -> None:
        """inline 模式下内容直接输出到终端，无需光标定位。

        保持公开接口兼容性，空操作。
        """
        pass

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
        """阶段 2：执行渲染命令（inline 模式）。

        inline 模式下内容直接输出到终端，无需 DECSTBM 同步或光标定位。
        遍历命令列表逐条分发给 TuiRenderer。
        单条命令失败时记录调试日志并入队错误提示，不中断循环。

        Args:
            commands: 一批待渲染的命令元组列表，每项格式为 (command_id, *args)
        """
        for cmd in commands:
            try:
                self._renderer.render(cmd)
            except Exception:
                _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                self.push_cmd((RenderCommand.ERROR, f"渲染命令 {_cmd_name(cmd[0])} 失败"))

    def _phase_redraw_bottom(self) -> None:
        """阶段 3：30Hz 定时重绘底部栏（inline 模式）。

        使用 _last_bottom_redraw 定时器确保每秒最多重绘 30 次。
        例外：prepare_for_content 已清除旧底栏时（_bar_cleared），
        强制立即重绘，防止出现空白栏。

        _bottom_redraw_requested 事件可作为「强制立即重绘」信号
        （由 request_bottom_redraw() 设置）。

        inline 模式下 force_redraw() 自行处理全部渲染+光标定位。
        """
        now = time.monotonic()
        force = self._bottom_redraw_requested.is_set()
        self._bottom_redraw_requested.clear()
        # prepare_for_content 已清除旧栏 → 必须立即重绘
        must_redraw = getattr(self._bb, '_bar_cleared', False)
        if force or must_redraw or now - self._last_bottom_redraw >= self._BOTTOM_REDRAW_INTERVAL:
            self._last_bottom_redraw = now
            try:
                self._bb.force_redraw()
            except Exception:
                _logger.debug("force_redraw 异常", exc_info=True)

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
                        # 指数退避平滑过渡：5ms → 10ms → 20ms → 40ms → 80ms → 100ms（钳位）
                        wait_timeout = min(
                            self._ACTIVE_RENDER_INTERVAL * (2 ** idle_count),
                            _RENDER_INTERVAL,
                        )
                        idle_count += 1
                        if idle_count > 10:
                            idle_count = 10
                    self._cmd_event.wait(timeout=wait_timeout)
                    if not has_content:
                        self._cmd_event.clear()
                except Exception as exc:
                    self._render_crashed.set()
                    try:
                        _logger.critical("idle_count=%d, cmd_queue.qsize=%d",
                                         idle_count, self._cmd_queue.qsize())
                        _logger.critical("render 线程异常崩溃", exc_info=True)
                        _emergency_write(
                            f"{_ANSI_RED}[ChatUI] render 线程异常终止: "
                            f"{type(exc).__name__}: {exc}{_ANSI_RESET}\n",
                            stream="stderr",
                        )
                    except Exception:
                        # 终端可能已完全不可用（如 PTY 断开），
                        # 不能因此跳过关键清理
                        pass
                    self._cmd_event.set()
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
        """三阶段流水线：预处理面板→清除旧底栏→获取输出锁→渲染命令→重绘底部栏。

        阶段 0: 清除旧底部栏 — prepare_for_content() 上行清除
        阶段 1: _phase_pre_update_panels() — 刷新面板回调（锁外执行）
        阶段 2: 获取输出锁，批量取出队列中所有命令
        阶段 3: _phase_render() 执行渲染命令，_phase_redraw_bottom() 重绘底部栏

        inline 模式：阶段 0 在锁外清除旧底部栏，确保内容从正确位置开始输出。
        性能优化：
        - 仅在面板回调注册时执行阶段 1（默认 None，跳过空调用）
        - 面板回调（CPU 渲染 + Queue.put）在锁外执行，减少 output_lock 持锁时间

        Returns:
            是否处理了至少一条渲染命令
        """
        t_start = time.monotonic()
        commands: list[tuple] = []
        # ★ 阶段 0：锁外清除旧底部栏（inline 模式）
        self._bb.prepare_for_content()
        # ★ 阶段 1：锁外执行面板刷新，减少持锁时间
        self._phase_pre_update_panels()
        with _try_acquire_output_lock(name="drain_queue", timeout=_DRAIN_LOCK_TIMEOUT) as locked:
            if not locked:
                return False
            # 容量钳位：单帧最多处理 _MAX_BATCH_SIZE 条命令，
            # 超出部分留待下一周期，防止 UI 冻结
            while len(commands) < _MAX_BATCH_SIZE:
                try:
                    commands.append(self._cmd_queue.get_nowait())
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            has_content = bool(commands)
            if commands:
                self._phase_render(commands)
            self._phase_redraw_bottom()

            # ── DEBUG 性能日志（队列深度 / 批处理大小 / 耗时） ──
            elapsed = (time.monotonic() - t_start) * 1000
            qdepth = self._cmd_queue.qsize()
            if has_content or elapsed > 5.0:
                _logger.debug(
                    "drain_queue: batch=%d depth=%d elapsed=%.2fms",
                    len(commands), qdepth, elapsed,
                )

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
        """inline 模式下 force_redraw() 已处理光标定位，空操作。

        保持方法签名兼容性，供 _phase_redraw_bottom 调用链使用。
        """
        pass


# @deprecated — 使用 TuiEngine/TuiRenderer 替代，v1.3+ 将移除
RenderEngine = TuiEngine
ContentRenderer = TuiRenderer
