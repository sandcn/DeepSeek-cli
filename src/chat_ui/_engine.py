"""chat_ui 渲染引擎模块 — Reader 线程 + 命令队列 + 渲染循环。

Layer 3 — 依赖 _const（_READER_INTERVAL）+ _renderers（ContentRenderer）
          + _state（_active_parallel_display）。
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
from typing import TYPE_CHECKING

from ._const import _READER_INTERVAL, _cmd_name, RenderCommand

if TYPE_CHECKING:
    from ..ui._bottom_bar import _BottomBar
    from ..ui.parallel.display import ParallelDisplay
    from ._renderers import ContentRenderer

_logger = logging.getLogger(__name__)


class RenderEngine:
    """渲染引擎 — 管理 Reader 线程和命令队列的消费循环。

    Reader 线程以 10Hz 轮询命令队列，串行执行渲染命令。
    _drain_queue() 执行三阶段流水线：上屏渲染 → 面板刷新 → 底部栏重绘。
    """

    # ── 队列满连续告警阈值（push_cmd 使用） ──
    _CONSECUTIVE_FULL_THRESHOLD = 10

    def __init__(
        self,
        renderer: "ContentRenderer",
        bottom_bar: "_BottomBar",
    ):
        self._renderer = renderer
        self._bb = bottom_bar

        # ── 渲染命令队列（线程安全，maxsize=10000 防 OOM） ──
        self._cmd_queue: queue.Queue = queue.Queue(maxsize=10000)
        self._cmd_event = threading.Event()

        # ── Reader 线程 ──
        self._reader_thread: threading.Thread | None = None
        self._reader_running = False

        # ── 队列满连续计数（超过阈值时直接警告用户） ──
        self._consecutive_full = 0

    # ── 公开 API ─────────────────────────────────────────

    def push_cmd(self, cmd: tuple) -> None:
        """向命令队列入队（线程安全，供 EventDispatcher 回调使用）。

        队列满时丢弃新命令并记录警告（不阻塞 EventDispatcher 回调线程）。
        """
        try:
            self._cmd_queue.put(cmd, block=False)
            self._cmd_event.set()
            self._consecutive_full = 0
        except queue.Full:
            self._consecutive_full += 1
            if cmd[0] == RenderCommand.ERROR:
                # ★ ERROR 命令走直写终端路径，确保用户可见
                msg = cmd[1] if len(cmd) > 1 else "未知错误"
                sys.__stdout__.write(
                    f"\033[31m! [ChatUI] 队列拥堵: {msg}\033[0m\n"
                )
                sys.__stdout__.flush()
            else:
                _logger.warning(
                    "渲染命令队列已满（%s 条），丢弃命令: %s",
                    self._cmd_queue.qsize(), _cmd_name(cmd[0]),
                )
            # ★ 连续满超过阈值时直接警告终端
            if self._consecutive_full >= self._CONSECUTIVE_FULL_THRESHOLD:
                sys.__stdout__.write(
                    "\033[33m[ChatUI] 渲染输出管线持续拥堵，部分内容可能丢失\033[0m\n"
                )
                sys.__stdout__.flush()

    def start(self) -> None:
        """启动 Reader 线程。

        三路分支防止双 Reader 线程：
        - 线程存活 → 跳过（不创建新线程，保持单线程）
        - 线程已死 → join 清理后创建新线程
        - 无旧线程 → 直接创建新线程

        不再使用 `_reader_thread = None` 清空引用，
        确保 stop() join 超时后 `is_alive()` 仍能准确判断线程状态。
        """
        if self._reader_thread is not None:
            if self._reader_thread.is_alive():
                _logger.warning(
                    "start() 被重复调用，但 Reader 线程仍在运行，跳过"
                )
                return
            # ★ 线程已死：join 清理（join 死线程立即返回）
            self._reader_thread.join()
        # ★ 线程 None 或已 join 清理完成：创建新线程
        self._reader_running = True
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        """停止 Reader 线程 + 关闭渲染器。

        join 超时（2s）后线程可能仍在运行，使用 cmd_event 循环唤醒
        （最多 3 次 × 0.5s），防止线程无限运行。

        不再清空 _reader_thread 引用 —— 保留死线程引用让 start()
        通过 is_alive() 准确判断线程真实状态，避免 start() 误判
        「无存活线程」而创建第二个 Reader 线程。
        """
        self._reader_running = False
        self._cmd_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            if self._reader_thread.is_alive():
                # ★ 超时后仍存活：用 cmd_event 多次唤醒
                for _ in range(3):
                    self._cmd_event.set()
                    self._reader_thread.join(timeout=0.5)
                    if not self._reader_thread.is_alive():
                        break

    def flush(self, timeout: float | None = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。

        Reader 未运行时直接清空队列（无人消费，等待无意义），
        Reader 运行时创建临时 daemon 线程消费 queue.join() 等待。

        参数:
            timeout: 最大等待秒数，超时后返回。默认 5 秒，None 表示无限等待。
        """
        self._cmd_event.set()
        if self._reader_thread is None or not self._reader_thread.is_alive():
            # Reader 线程从未启动或已终止；直接清空队列避免虚假等待
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        # Reader 线程存在（可能仍在运行）；通过 queue.join() 等待消费完毕
        task_done = threading.Thread(
            target=self._cmd_queue.join, daemon=True,
        )
        task_done.start()
        task_done.join(timeout=timeout)

    def ensure_cursor_upper(self) -> None:
        """将光标移到内容区。调用方须持有 output_lock。"""
        self._bb.ensure_cursor_in_upper()

    # ── 内部 — Reader 线程 ────────────────────────────

    def _reader(self) -> None:
        """Reader 线程入口。"""
        while self._reader_running:
            try:
                self._drain_queue()
                self._cmd_event.wait(timeout=_READER_INTERVAL)
                self._cmd_event.clear()
            except Exception:
                _logger.critical(
                    "Reader 线程异常崩溃，终止",
                    exc_info=True,
                )
                # 直接写 stderr 确保用户可见（Reader 线程已死，队列无人消费）
                sys.__stderr__.write(
                    "\033[31m[ChatUI] Reader 线程异常终止，请联系开发人员查看日志\033[0m\n"
                )
                sys.__stderr__.flush()
                self._reader_running = False
                break

    def _drain_queue(self) -> None:
        """消费所有待处理渲染命令，执行上屏渲染 + 底部栏重绘。

        流水线：
          0. 快速空闲跳过（锁外）— 队列空 + 无面板 + 状态行不活跃 + 无 resize pending 时跳过
          0b. 终端大小变化检测（锁外）— 检测 resize 并刷新渲染器宽度缓存
          1. resize 处理（锁内）— 消费 SIGWINCH 标记，更新终端尺寸和 DECSTBM
          2. 上屏渲染（锁内）— 批量出队 + 渲染命令
          3. ParallelDisplay 面板刷新（锁内）— 刷新 SubAgent UI 面板
          4. 底部栏重绘 + 光标定位（锁内）— force_redraw + 光标移回输入行

        步骤 0/0b 在锁外、步骤 1-4 共用同一个 output_lock，
        防止上屏渲染 / 面板刷新 / 底部栏重绘之间的终端 I/O 交错。
        output_lock 为 RLock（可重入），pd.refresh() 和 force_redraw()
        内部取锁不会死锁。

        ParallelDisplay 刷新置于渲染阶段之后：先渲染上屏内容（工具输出/摘要等），
        再刷新 SubAgent UI 面板展示最新状态，确保面板状态与已渲染内容同步。
        """
        from ..ui._lock import _try_acquire_output_lock

        from . import _active_parallel_display
        pd = _active_parallel_display
        if (self._cmd_queue.empty() and pd is None
                and not self._bb.is_status_active
                and not self._bb.is_resize_pending):
            return

        # ★ 终端大小变化检测（锁外）— 避免 shutil.get_terminal_size()
        #   系统调用在 output_lock 内执行，减少锁持有时间。
        #   异常静默忽略：检测失败时降级依赖 OutputAdapter 的 5 秒 TTL。
        try:
            self._renderer._check_and_refresh_width()
        except Exception:
            pass

        # ★ 三个阶段共用同一个 output_lock（1s 超时）
        with _try_acquire_output_lock(name="drain_queue", timeout=1.0) as locked:
            if not locked:
                return

            # ★ 处理待处理的终端 resize（SIGWINCH 标记），在渲染前更新终端状态
            resized = False
            try:
                resized = self._bb.check_resize()
            except Exception:
                _logger.debug(
                    "drain_queue: check_resize 异常", exc_info=True,
                )

            # ★ 阶段 1：批量出队 + 上屏渲染
            commands: list[tuple] = []
            while True:
                try:
                    commands.append(self._cmd_queue.get_nowait())
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            if commands:
                # ★ 非 resize 路径：sync DECSTBM + 光标移到内容区底部再渲染
                #   resize 时 check_resize() 已同步 DECSTBM 和光标预定位（Fix A），
                #   无需重复调用 sync_bottom_lines 和 ensure_cursor_upper。
                if not resized:
                    try:
                        self._bb.sync_bottom_lines()
                    except Exception:
                        _logger.debug(
                            "drain_queue: sync_bottom_lines 异常",
                            exc_info=True,
                        )
                    self.ensure_cursor_upper()
                for cmd in commands:
                    try:
                        self._renderer.render(cmd)
                    except Exception:
                        _logger.debug(
                            "drain_queue: 渲染命令 %s 失败", cmd,
                            exc_info=True,
                        )
                        self.push_cmd((
                            RenderCommand.ERROR,
                            f"渲染命令 {_cmd_name(cmd[0])} 失败，请查看日志获取详情",
                        ))
                sys.__stdout__.flush()

            # ★ 阶段 2：ParallelDisplay 面板刷新
            if pd is not None:
                try:
                    pd.refresh()
                except Exception:
                    _logger.debug(
                        "drain_queue: ParallelDisplay 刷新异常",
                        exc_info=True,
                    )
                    self.push_cmd((
                        RenderCommand.ERROR,
                        "drain_queue: ParallelDisplay 刷新失败，请查看日志获取详情",
                    ))

            # ★ 阶段 3：底部栏重绘 + 光标定位
            # 分流策略：
            #   - 有命令 → 全量重绘
            #   - 仅流式活跃（无命令）→ 每帧全量底部栏重绘
            #     使用 force_redraw，流式输出期间
            #     _format_status() 每帧返回不同文本（令牌数/速率/耗时变化），
            #     force_redraw 的快速路径（new_status == _last_status）不触发，
            #     确保每帧完整刷新底部栏全部内容（分隔线+状态行+输入区）。
            if commands or self._bb.is_status_active:
                try:
                    self._bb.force_redraw()
                except Exception:
                    _logger.debug(
                        "drain_queue: force_redraw 异常", exc_info=True,
                    )
                try:
                    self._position_cursor()
                except Exception:
                    _logger.debug(
                        "drain_queue: _position_cursor 异常", exc_info=True,
                    )

    def _position_cursor(self) -> None:
        """光标移回输入行，根据超长文本自动拆行定位（含最少3行输入区）。

        使用 _BottomBar._cursor_visual_pos_from_cache 复用拆行缓存，
        避免每次 drain 周期都做 O(n·wcswidth) 的 _compute_cursor_visual_pos 重算。
        """
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        max_input = max(1, w - 4)

        vis_row, vis_col = self._bb._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
        total_bottom = max(5, self._bb._bottom_lines)  # 至少 2 分隔线+状态行 + 3 最少输入行
        popup_offset = self._bb._completion_popup_height
        r_cursor = max(1, h - total_bottom + 3 + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()
