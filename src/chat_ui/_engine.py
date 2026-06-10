"""chat_ui 渲染引擎模块 — render 线程 + 命令队列 + 渲染循环。

Layer 3 — 依赖 _const（_RENDER_INTERVAL）+ _renderers（ContentRenderer）。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import Callable, TYPE_CHECKING

from ._const import (
    _ANSI_RED,
    _ANSI_RESET,
    _RENDER_INTERVAL,
    RenderCommand,
)
from ._utils import _cmd_name
from ..ui._blessed import get_terminal
from ..ui._lock import _try_acquire_output_lock

if TYPE_CHECKING:
    from ._protocols import BottomBarProtocol
    from ._renderers import ContentRenderer

_logger = logging.getLogger(__name__)


class RenderEngine:
    """渲染引擎 — 管理 render 线程和命令队列的消费循环。

    render 线程以 10Hz 轮询命令队列，串行执行渲染命令。
    _drain_queue() 执行三阶段流水线：上屏渲染 → 面板刷新 → 底部栏重绘。
    """

    # ── 队列满连续告警阈值（push_cmd 使用） ──
    _CONSECUTIVE_FULL_THRESHOLD = 10

    def __init__(
        self,
        renderer: "ContentRenderer",
        bottom_bar: "BottomBarProtocol",
    ):
        self._renderer = renderer
        self._bb = bottom_bar

        # ── 渲染命令队列（线程安全，maxsize=10000 防 OOM） ──
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._cmd_event = threading.Event()

        # ── render 线程 ──
        self._render_thread: threading.Thread | None = None
        self._render_running = False

        # ── 队列满连续计数（超过阈值时直接警告用户） ──
        self._consecutive_full = 0

        # ── 底部栏重绘请求标志（线程安全 Event，替代废弃的 BOTTOM_BAR_REFRESH 命令）
        #     由 ChatUIConsumer.request_bottom_redraw() / refresh_bottom_bar() 设置，
        #     在 _phase_redraw_bottom() 中消费并清除。
        self._bottom_redraw_requested = threading.Event()

        # ── 面板刷新回调（由 ParallelDisplay 在 start() 中注册，
        #     在 _phase_refresh_panels() 中被 render 线程 10Hz 调用） ──
        self._panel_refresh_cb: Callable[[], None] | None = None

    # ── 公开 API ─────────────────────────────────────────

    def push_cmd(self, cmd: tuple) -> None:
        """向命令队列入队（线程安全，供 EventDispatcher 回调使用）。

        队列满时丢弃新命令并记录警告（不阻塞 EventDispatcher 回调线程）。
        ERROR 命令优先尝试阻塞入队（最多等 0.5s），避免绕过 output_lock 直写终端。
        """
        try:
            self._cmd_queue.put(cmd, block=False)
            self._cmd_event.set()
            self._consecutive_full = 0
        except queue.Full:
            self._consecutive_full += 1
            if cmd[0] == RenderCommand.ERROR:
                # ★ ERROR 命令先尝试阻塞入队（最多等 0.5s），
                #   避免绕过 output_lock 直写终端导致 I/O 交错
                try:
                    self._cmd_queue.put(cmd, block=True, timeout=0.5)
                    self._cmd_event.set()
                    self._consecutive_full = 0
                except queue.Full:
                    _logger.warning(
                        "渲染命令队列已满，ERROR 命令入队超时丢弃: %s",
                        self._cmd_queue.qsize(),
                    )
            else:
                _logger.warning(
                    "渲染命令队列已满（%s 条），丢弃命令: %s",
                    self._cmd_queue.qsize(), _cmd_name(cmd[0]),
                )
            # ★ 连续满超过阈值时记录日志（不再写终端以免 I/O 交错）
            if self._consecutive_full >= self._CONSECUTIVE_FULL_THRESHOLD:
                _logger.error(
                    "渲染输出管线持续拥堵（%d 次连续满队列），部分内容可能丢失",
                    self._consecutive_full,
                )

    def set_panel_refresh_callback(
        self, callback: Callable[[], None] | None,
    ) -> None:
        """设置面板刷新回调，由 render 线程的 _phase_refresh_panels() 以 10Hz 调用。

        Args:
            callback: 无参回调，或 None 来注销。
        """
        self._panel_refresh_cb = callback

    def request_bottom_redraw(self) -> None:
        """请求 render 线程重绘底部栏（线程安全）。

        替代废弃的 push_cmd((RenderCommand.BOTTOM_BAR_REFRESH,)) 模式，
        使用 threading.Event 标志位避免无意义命令入队。
        同时设置 _cmd_event 立即唤醒 render 线程。
        """
        self._bottom_redraw_requested.set()
        self._cmd_event.set()

    def start(self) -> None:
        """启动 render 线程。

        三路分支防止双 render 线程：
        - 线程存活 → 跳过（不创建新线程，保持单线程）
        - 线程已死 → join 清理后创建新线程
        - 无旧线程 → 直接创建新线程

        不再使用 `_render_thread = None` 清空引用，
        确保 stop() join 超时后 `is_alive()` 仍能准确判断线程状态。
        """
        if self._render_thread is not None:
            if self._render_thread.is_alive():
                _logger.warning(
                    "start() 被重复调用，但 render 线程仍在运行，跳过"
                )
                return
            # ★ 线程已死：join 清理（join 死线程立即返回）
            self._render_thread.join()
        # ★ 线程 None 或已 join 清理完成：创建新线程
        self._render_running = True
        self._render_thread = threading.Thread(target=self._render, daemon=True)
        self._render_thread.start()

    def stop(self) -> None:
        """停止 render 线程 + 关闭渲染器。

        join 超时（2s）后线程可能仍在运行，使用 cmd_event 循环唤醒
        （最多 3 次 × 0.5s），防止线程无限运行。

        不再清空 _render_thread 引用 —— 保留死线程引用让 start()
        通过 is_alive() 准确判断线程真实状态，避免 start() 误判
        「无存活线程」而创建第二个 render 线程。
        """
        self._render_running = False
        self._cmd_event.set()
        if self._render_thread is not None:
            self._render_thread.join(timeout=2.0)
            if self._render_thread.is_alive():
                # ★ 超时后仍存活：用 cmd_event 多次唤醒
                for _ in range(3):
                    self._cmd_event.set()
                    self._render_thread.join(timeout=0.5)
                    if not self._render_thread.is_alive():
                        break
        # ★ 线程已确认停止后，清空队列中残留命令
        #   （线程停止后无消费者，留存在队列中无意义且可能泄漏）
        self._drain_queue_safe()

    def flush(self, timeout: float | None = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。

        render 未运行时直接清空队列（无人消费，等待无意义），
        render 运行时创建临时 daemon 线程消费 queue.join() 等待。

        参数:
            timeout: 最大等待秒数，超时后返回。默认 5 秒，None 表示无限等待。
        """
        self._cmd_event.set()
        if self._render_thread is None or not self._render_thread.is_alive():
            # render 线程从未启动或已终止；直接清空队列避免虚假等待
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        # render 线程存在（可能仍在运行）；通过 queue.join() 等待消费完毕
        task_done = threading.Thread(
            target=self._cmd_queue.join, daemon=True,
        )
        task_done.start()
        task_done.join(timeout=timeout)

    def ensure_cursor_upper(self) -> None:
        """将光标移到内容区。调用方须持有 output_lock。"""
        self._bb.ensure_cursor_in_upper()

    # ── 内部 — 三阶段流水线 ──────────────────────────

    def _phase_render(self, commands: list[tuple]) -> None:
        """阶段 1：批量出队 + 上屏渲染（在 output_lock 内调用）。

        参数:
            commands: 待渲染的命令列表
        """
        try:
            self._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("drain_queue: sync_bottom_lines 异常", exc_info=True)
        self.ensure_cursor_upper()
        for cmd in commands:
            try:
                self._renderer.render(cmd)
            except Exception:
                _logger.debug("drain_queue: 渲染命令 %s 失败", cmd, exc_info=True)
                self.push_cmd((
                    RenderCommand.ERROR,
                    f"渲染命令 {_cmd_name(cmd[0])} 失败，请查看日志获取详情",
                ))
        sys.__stdout__.flush()

    def _phase_refresh_panels(self) -> None:
        """阶段 2：面板刷新 — 调用外部注册的刷新回调（如 ParallelDisplay）。

        由 render 线程在 output_lock 保护下以 10Hz 频率调用，
        用于驱动 SubAgent 面板的耗时显示更新（elapsed time），
        消除独立的 500ms 定时刷新任务。
        """
        if self._panel_refresh_cb is not None:
            try:
                self._panel_refresh_cb()
            except Exception:
                _logger.warning(
                    "panel_refresh_cb 异常", exc_info=True,
                )

    def _phase_redraw_bottom(self, has_commands: bool) -> None:
        """阶段 3：底部栏重绘 + 光标定位（在 output_lock 内调用）。

        分流策略：
        - has_commands=True → 全量重绘
        - is_status_active → 流式状态每帧强制重绘
        """
        redraw = has_commands or self._bottom_redraw_requested.is_set() or self._bb.is_status_active
        self._bottom_redraw_requested.clear()
        if redraw:
            try:
                self._bb.force_redraw()
            except Exception:
                _logger.debug("drain_queue: force_redraw 异常", exc_info=True)
            try:
                self.position_cursor()
            except Exception:
                _logger.debug("drain_queue: position_cursor 异常", exc_info=True)

    # ── 内部 — render 线程 ────────────────────────────

    def _render(self) -> None:
        """render 线程入口。

        try/finally 确保线程无论正常/异常退出都清空命令队列，
        避免队列残留命令被后续线程 (flush/drain_queue_safe) 消费时
        引发 task_done 计数不匹配或队列残留。
        """
        try:
            while self._render_running:
                try:
                    self._drain_queue()
                    self._cmd_event.wait(timeout=_RENDER_INTERVAL)
                    self._cmd_event.clear()
                except Exception:
                    _logger.critical(
                        "render 线程异常崩溃，终止",
                        exc_info=True,
                    )
                    # 直接写 stderr 确保用户可见（render 线程已死，队列无人消费）
                    sys.__stderr__.write(
                        f"{_ANSI_RED}[ChatUI] render 线程异常终止，"
                        f"请联系开发人员查看日志{_ANSI_RESET}\n"
                    )
                    sys.__stderr__.flush()
                    self._render_running = False
                    break
        finally:
            # ★ 确保所有退出路径都清空队列（包含正常退出、KeyboardInterrupt 等）
            self._drain_queue_safe()

    def _drain_queue(self) -> None:
        """消费所有待处理渲染命令。

        全部三阶段在 output_lock 保护下执行：
          1. 批量出队渲染命令
          2. 上屏渲染 → _phase_render()
          3. 面板刷新 → _phase_refresh_panels()
          4. 底部栏重绘 + 光标定位 → _phase_redraw_bottom()
        """
        commands: list[tuple] = []
        with _try_acquire_output_lock(name="drain_queue", timeout=1.0) as locked:
            if not locked:
                return

            # ★ 批量出队
            while True:
                try:
                    commands.append(self._cmd_queue.get_nowait())
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break

            has_content = bool(commands)

            if commands:
                self._phase_render(commands)

            self._phase_refresh_panels()

            self._phase_redraw_bottom(has_content)

    def _drain_queue_safe(self) -> None:
        """兜底清空命令队列（无锁、不抛异常）。

        用于 render 线程异常退出路径（_render() except 块 + finally 块），
        确保已入队但未被消费的命令在线程终止前被清空。
        不持有 output_lock——线程已终止，下游已无消费者，
        直接清空队列避免残留命令对后续 flush() 造成干扰。

        与 flush() 的区别：
        - flush() 等待 render 线程消费队列（需要锁/output_lock）
        - _drain_queue_safe() 直接清空队列（无锁，仅在线程死亡后调用）
        """
        while not self._cmd_queue.empty():
            try:
                self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
            except queue.Empty:
                break
            except Exception:
                break

    def position_cursor(self) -> None:
        """公开方法 — 将光标移回输入行，根据超长文本自动拆行定位。

        通过 _BottomBar.compute_cursor_position() 公开 API 计算光标位置，
        避免直接访问 _BottomBar 的私有属性。
        """
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        r_cursor, cursor_col = self._bb.compute_cursor_position(text, cursor_pos, h, w)
        try:
            term = get_terminal()
            sys.__stdout__.write(term.move_xy(cursor_col - 1, r_cursor - 1))
        except Exception:
            sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()
