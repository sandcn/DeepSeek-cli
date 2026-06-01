"""chat_ui 渲染引擎模块 — Reader 线程 + 命令队列 + 渲染循环。

Layer 3 — 依赖 _const（_READER_INTERVAL）+ _renderers（ContentRenderer）
          + _state（_active_parallel_display）。
"""

from __future__ import annotations

import logging
import queue
import shutil
import sys
import threading
import time
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
    _drain_queue() 执行四阶段流水线：尺寸检测 → 上屏渲染 → 面板刷新 → 底部栏重绘。
    """

    def __init__(
        self,
        renderer: "ContentRenderer",
        bottom_bar: "_BottomBar",
    ):
        self._renderer = renderer
        self._bb = bottom_bar

        # ── 渲染命令队列（线程安全） ──
        self._cmd_queue: queue.Queue = queue.Queue()
        self._cmd_event = threading.Event()

        # ── Reader 线程 ──
        self._reader_thread: threading.Thread | None = None
        self._reader_running = False

    # ── 公开 API ─────────────────────────────────────────

    def push_cmd(self, cmd: tuple) -> None:
        """向命令队列入队（线程安全，供 EventDispatcher 回调使用）。"""
        self._cmd_queue.put(cmd)
        self._cmd_event.set()

    def start(self) -> None:
        """启动 Reader 线程。"""
        self._reader_running = True
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def stop(self) -> None:
        """停止 Reader 线程 + 关闭渲染器。"""
        self._reader_running = False
        self._cmd_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            if not self._reader_thread.is_alive():
                self._reader_thread = None

    def flush(self, timeout: float | None = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。

        Reader 未运行时直接清空队列（无人消费，等待无意义），
        Reader 运行时创建临时 daemon 线程消费 queue.join() 等待。

        参数:
            timeout: 最大等待秒数，超时后返回。默认 5 秒，None 表示无限等待。
        """
        self._cmd_event.set()
        if self._reader_thread is None:
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
            self._drain_queue()
            self._cmd_event.wait(timeout=_READER_INTERVAL)
            self._cmd_event.clear()

    def _drain_queue(self) -> None:
        """消费所有待处理渲染命令，执行上屏渲染 + 底部栏重绘。

        四阶段流水线：
          0. 快速空闲跳过 + 尺寸检测
          1. 上屏渲染（1s 超时，在锁内出队+渲染，避免命令丢失）
          2. ParallelDisplay 面板刷新（上屏渲染完成后立即刷新面板状态）
          3. 底部栏重绘 + 光标定位（1s 超时，超时则跳过本轮重绘）

        ParallelDisplay 刷新置于渲染阶段之后：先渲染上屏内容（工具输出/摘要等），
        再刷新 SubAgent UI 面板展示最新状态，确保面板状态与已渲染内容同步。
        """
        from ..ui._lock import _try_acquire_output_lock

        # ★ 快速空闲跳过：无待处理命令、无面板、非流式、无待处理 resize 时跳过全部 I/O。
        #   10Hz drain 中约 70%+ 周期为空闲（流式外时段），
        #   此检查避免 3 次锁获取 + syscall（shutil.get_terminal_size）+ ANSI I/O。
        #   排除 is_resize_pending：SIGWINCH 已触发但未被消费时，
        #   即使无流式输出也必须穿透跳过，执行 _check_resize() 修复 DECSTBM。
        from . import _active_parallel_display
        pd = _active_parallel_display
        if (self._cmd_queue.empty() and pd is None
                and not self._bb.is_status_active
                and not self._bb.is_resize_pending):
            return

        # ★ Fix A: 保存 resize 前尺寸，供 resize 后 Stage 1 光标定位使用。
        #   在 resize 检测前保存，此时 _setup_height / _last_bottom_lines 仍为旧值。
        #   终端变高时，旧内容仍在屏幕上方的旧位置。若将光标放新 scroll_end（最末行），
        #   渲染内容时每个 \n 会触发 DECSTBM 滚动导致旧内容被逐行滚出清空。
        #   改为定位到旧内容末尾（min(old_scroll_end+1, new_scroll_end)），新内容从
        #   旧末行开始填充间隙，填满后才触发正常滚动。
        if self._bb._active:
            _pre_height = self._bb._setup_height
            _pre_bottom = self._bb._last_bottom_lines
        else:
            _pre_height = 0
            _pre_bottom = 0

        # ★ 尺寸检测：持锁调用以与 refresh()/force_redraw() 串行化
        with _try_acquire_output_lock(name="drain_queue.resize", timeout=1.0) as locked:
            resized = locked and self._bb.check_resize()
        if resized:
            self._sync_renderer_width()

        # ★ 阶段 1：锁内批量出队 + 上屏渲染（出队与渲染原子化，消除命令丢失窗口）
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
                    if resized and _pre_height > 0:
                        height = self._bb._term_height()
                        new_s = height - self._bb._bottom_lines
                        if height > _pre_height:
                            # ★ Fix A: 终端变高，光标定位到旧内容末尾
                            #   避免在 new_scroll_end 写内容触发 DECSTBM 滚动
                            target = max(1, min(_pre_height - _pre_bottom + 1, new_s))
                            sys.__stdout__.write(f"\033[{target};1H")
                        else:
                            # ★ 修复: 终端变小时也定位到旧内容末尾，避免在新 scroll_end
                            #   写内容触发 DECSTBM 滚动导致旧内容被逐行滚出清空。
                            #   旧内容末行 = _pre_height - _pre_bottom + 1，
                            #   但缩小后被底部栏挡住的部分需要放弃，所以 clamp 到 new_s。
                            old_end = max(1, _pre_height - _pre_bottom + 1)
                            target = max(1, min(old_end, new_s))
                            sys.__stdout__.write(f"\033[{target};1H")
                    else:
                        self.ensure_cursor_upper()
                    for cmd in commands:
                        try:
                            self._renderer.render(cmd)
                        except Exception:
                            _logger.debug(
                                "drain_queue: 渲染命令 %s 失败", cmd,
                                exc_info=True,
                            )
                            self._cmd_queue.put((
                                RenderCommand.ERROR,
                                f"渲染命令 {_cmd_name(cmd[0])} 失败，请查看日志获取详情",
                            ))
                    sys.__stdout__.flush()

        # ★ 阶段 2：ParallelDisplay 面板刷新（无锁，render_frame 内部用
        #   timeout try-lock 保护终端 I/O）。
        #   顺序说明：上屏渲染完成后立即刷新面板，确保 SubAgent 状态面板
        #   反映的是最新执行结果，不与底部栏重绘交错。
        if pd is not None:
            try:
                pd.refresh()
            except Exception:
                _logger.debug(
                    "drain_queue: ParallelDisplay 刷新异常",
                    exc_info=True,
                )
                self._cmd_queue.put((
                    RenderCommand.ERROR,
                    "drain_queue: ParallelDisplay 刷新失败，请查看日志获取详情",
                ))

        # ★ 阶段 2.5（B4 fix）：Stage 1/2 渲染期间可能再次 resize，
        #   在 Stage 3 force_redraw 前快速重检测，消除上下屏宽度不一致窗口
        with _try_acquire_output_lock(name="drain_queue.resize_pre3", timeout=0.5) as locked:
            if locked and self._bb.check_resize():
                resized = True
                self._sync_renderer_width()  # ★ B4/B7 fix: 同步更新 Console 宽度

        # ★ 阶段 3：底部栏重绘 + 光标定位
        # 分流策略：
        #   - 有命令/尺寸变化 → 全量重绘（force_redraw，跳过内部 _check_resize
        #     因 Stage 0b/2.5 已完成检测，消除双调用窗口）
        #   - 仅流式活跃（无命令/无尺寸变化）→ 每帧全量底部栏重绘
        #     使用 force_redraw（跳过内部 _check_resize），流式输出期间
        #     _format_status() 每帧返回不同文本（令牌数/速率/耗时变化），
        #     force_redraw 的快速路径（new_status == _last_status）不触发，
        #     确保每帧完整刷新底部栏全部内容（分隔线+状态行+输入区）。
        if commands or resized:
            with _try_acquire_output_lock(name="drain_queue.bottom", timeout=1.0) as locked:
                if locked:
                    self._bb.force_redraw(skip_resize_check=True)  # Bug 8: 跳过内部 _check_resize
                    self._position_cursor()
        elif self._bb.is_status_active:
            with _try_acquire_output_lock(name="drain_queue.streaming_redraw", timeout=1.0) as locked:
                if locked:
                    self._bb.force_redraw(skip_resize_check=True)
                    self._position_cursor()

    def _position_cursor(self) -> None:
        """光标移回输入行，根据超长文本自动拆行定位（含最少3行输入区）。

        使用 _BottomBar._cursor_visual_pos_from_cache 复用拆行缓存，
        避免每次 drain 周期都做 O(n·wcswidth) 的 _compute_cursor_visual_pos 重算。
        """
        text, cursor_pos, h, w = self._bb.get_cursor_info()
        max_input = max(1, w - 4)

        vis_row, vis_col = self._bb._cursor_visual_pos_from_cache(text, cursor_pos, max_input)
        total_bottom = self._bb._bottom_lines
        popup_offset = self._bb._completion_popup_height
        r_cursor = max(1, h - total_bottom + 3 + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()

    def _sync_renderer_width(self) -> None:
        """resize 后同步更新所有活跃 Rich Console 的宽度，消除 5s TTL 缓存滞后（B7 fix）。

        遍历 _RenderState 中所有活跃渲染器（推理/内容/工具输出），
        强制设置其 console.width = 新终端宽度，使后续渲染立即使用新宽度换行。
        """
        try:
            new_width = shutil.get_terminal_size().columns
        except Exception:
            return
        if new_width <= 0:
            return

        rs = self._renderer._rs

        now = time.monotonic()

        # 推理渲染器的 OutputAdapter
        rr = rs.reasoning
        if rr is not None and hasattr(rr, '_output') and hasattr(rr._output, '_console'):
            rr._output._console.width = new_width
            if hasattr(rr._output, '_width'):
                rr._output._width = new_width
            if hasattr(rr._output, '_last_width_refresh'):
                rr._output._last_width_refresh = now

        # 内容渲染器的 OutputAdapter
        cr = rs.content
        if cr is not None and hasattr(cr, '_output') and hasattr(cr._output, '_console'):
            cr._output._console.width = new_width
            if hasattr(cr._output, '_width'):
                cr._output._width = new_width
            if hasattr(cr._output, '_last_width_refresh'):
                cr._output._last_width_refresh = now

        # 工具输出适配器的 OutputAdapter（可能尚未惰性创建）
        if rs._tool_adapter is not None and hasattr(rs._tool_adapter, '_console'):
            rs._tool_adapter._console.width = new_width
            if hasattr(rs._tool_adapter, '_width'):
                rs._tool_adapter._width = new_width
            if hasattr(rs._tool_adapter, '_last_width_refresh'):
                rs._tool_adapter._last_width_refresh = now
