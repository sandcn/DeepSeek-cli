"""渲染引擎 — TuiEngine + render 线程 + 命令队列。

从 _tui.py 拆分，管理三阶段渲染流水线（预更新面板→获取输出锁→渲染命令→重绘底部栏）。
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ._protocols import BottomBarProtocol

from ._renderer import TuiRenderer, RichLiveContentRenderer
from ._const import (
    _RENDER_INTERVAL,
    _DRAIN_LOCK_TIMEOUT,
    _ANSI_RED, _ANSI_RESET,
    _FIXED_FRAME_INTERVAL,
    _ENV_FIXED_FPS,
)

from ._cmd import (
    CmdReasoning,
    CmdContent,
    CmdError,
)

from ._utils import _cmd_name  # noqa: F401 — 保留供旧代码兼容

from ._lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)

# ── 引擎常量 ──────────────────────────────────────

_ACTIVE_RENDER_INTERVAL = 0.005
_IDLE_DRAIN_THRESHOLD = 5
_CONSECUTIVE_FULL_THRESHOLD = 10


# ═══════════════════════════════════════════════════════════
# TuiEngine — 渲染引擎
# ═══════════════════════════════════════════════════════════

class TuiEngine:
    """渲染引擎 — render 线程 + Queue 命令队列 + 三阶段渲染循环。

    组件化架构：所有内容通过 TuiRenderer 渲染，底部栏由 BottomBarProtocol 管理。
    """

    # 类级常量（从模块常量复制，允许测试通过实例属性覆盖）
    _ACTIVE_RENDER_INTERVAL = _ACTIVE_RENDER_INTERVAL
    _IDLE_DRAIN_THRESHOLD = _IDLE_DRAIN_THRESHOLD
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
        self._full_lock = threading.Lock()
        self._bottom_redraw_requested = threading.Event()
        self._panel_refresh_cb: Callable[[], None] | None = None

        # Rich Live 差分渲染（默认关闭，通过环境变量启用）
        self._use_rich_live: bool = (
            os.environ.get('CHAT_UI_RENDER_USE_RICH_LIVE', '').strip().lower()
            in ('1', 'true', 'yes', 'on')
        )
        self._rich_renderer: RichLiveContentRenderer | None = None
        if self._use_rich_live:
            # 使用 getattr 访问 _rs 以避免直接访问私有属性
            _render_state = getattr(renderer, '_rs', None)
            if _render_state is not None:
                self._rich_renderer = RichLiveContentRenderer(
                    _render_state, renderer.output_adapter
                )
            if not self._rich_renderer.available:
                _logger.warning("Rich Live 不可用（缺少 rich 库），回退到手动渲染")
                self._rich_renderer = None
                self._use_rich_live = False

        # ── 固定帧率渲染（默认关闭，通过环境变量启用） ──
        self._use_fixed_fps: bool = (
            os.environ.get(_ENV_FIXED_FPS, '').strip().lower()
            in ('1', 'true', 'yes', 'on')
        )
        if self._use_fixed_fps:
            _logger.info("固定帧率渲染已启用（%.0f fps）", 1.0 / _FIXED_FRAME_INTERVAL)

    def push_cmd(self, cmd) -> None:
        """入队渲染命令到命令队列。

        接受 RenderCommand dataclass 实例（如 CmdContent / CmdReasoning 等），
        非阻塞写入，队列满时优先合并同类型 CONTENT/REASONING 命令。

        Args:
            cmd: 渲染命令 dataclass 实例
        """
        try:
            self._cmd_queue.put(cmd, block=False)
            with self._full_lock:
                self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            # 合并逻辑：CONTENT/REASONING 同类型合并 text
            # ★ 使用 cmd_queue.mutex 保护 deque 的并发访问
            if isinstance(cmd, (CmdContent, CmdReasoning)):
                try:
                    with self._cmd_queue.mutex:
                        dq = self._cmd_queue.queue
                        if dq and type(dq[-1]) is type(cmd):
                            merged_text = dq[-1].text + cmd.text
                            new_cmd = type(cmd)(text=merged_text)
                            dq[-1] = new_cmd
                            with self._full_lock:
                                self._consecutive_full = 0
                            self._cmd_event.set()
                            return
                except (AttributeError, IndexError, TypeError):
                    pass
            with self._full_lock:
                self._consecutive_full += 1
                full_count = self._consecutive_full
            _logger.warning("渲染命令队列已满（%s 条），丢弃命令: %s",
                             self._cmd_queue.qsize(), type(cmd).__name__)
            if full_count >= self._CONSECUTIVE_FULL_THRESHOLD:
                _logger.error("渲染输出管线持续拥堵（%d 次连续满队列）", full_count)

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
        if self._rich_renderer is not None:
            try:
                self._rich_renderer.start()
            except Exception:
                _logger.warning("Rich Live 启动失败", exc_info=True)

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
        if self._rich_renderer is not None:
            try:
                self._rich_renderer.stop()
            except Exception:
                _logger.warning("Rich Live 停止失败", exc_info=True)

    def flush(self, timeout: float | None = 5.0) -> None:
        if self._render_thread is None or not self._render_thread.is_alive():
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        # 唤醒渲染线程避免卡在 wait() 而非处理队列
        self._cmd_event.set()
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

    def _phase_render(self, commands: list) -> None:
        """阶段 2：执行渲染命令。

        当 _use_rich_live=True 时，CONTENT/REASONING 命令通过 Rich Live
        差分渲染（减少手动 ANSI 刷新），其他命令仍走 TuiRenderer 直出。
        底部栏 DECSTBM 管理不受影响。

        Args:
            commands: 一批待渲染的命令元组列表，每项格式为 (command_id, *args)
        """
        try:
            self._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        self.ensure_cursor_upper()

        if self._use_rich_live and self._rich_renderer is not None:
            # Rich Live 路径：内容命令走差分渲染，其他命令直出
            try:
                for cmd in commands:
                    if isinstance(cmd, (CmdContent, CmdReasoning)):
                        self._rich_renderer.update_content(cmd.text)
                    else:
                        try:
                            self._renderer.render(cmd)
                        except Exception:
                            _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                            self.push_cmd(CmdError(message=f"渲染命令 {type(cmd).__name__} 失败"))
                self._rich_renderer.refresh()
            except Exception:
                _logger.debug("Rich Live 渲染异常", exc_info=True)
        else:
            # 默认路径：逐条分发渲染
            for cmd in commands:
                try:
                    self._renderer.render(cmd)
                except Exception:
                    _logger.debug("渲染命令 %s 失败", cmd, exc_info=True)
                    self.push_cmd(CmdError(message=f"渲染命令 {type(cmd).__name__} 失败"))

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

        支持两种模式（通过 CHAT_UI_RENDER_FIXED_FPS 环境变量切换）：
          - 默认：Event.wait 自适应等待模式（原有行为）
          - 固定帧率：固定 16ms (60fps) 循环，帧内批量 drain

        异常时记录 critical 日志并终止循环。
        退出时（finally）安全排空命令队列。
        """
        if self._use_fixed_fps:
            self._render_fixed_fps()
        else:
            self._render_adaptive()

    def _render_fixed_fps(self) -> None:
        """固定帧率渲染循环（60fps / 16ms）。

        每帧批量 drain 命令队列、执行渲染、重绘底部栏。
        不使用 _cmd_event 信号，完全由固定帧间隔驱动。

        空帧时防御性清理底部栏重绘标记（该标记由 _drain_queue → _phase_redraw_bottom 消费，
        此处仅清理空闲帧的过期标记，避免下一帧误触发冗余重绘）。
        """
        import time as _time_mod
        while self._render_running:
            frame_start = _time_mod.monotonic()
            try:
                has_content = self._drain_queue()
                if not has_content and not self._bottom_redraw_requested.is_set():
                    self._bottom_redraw_requested.clear()
            except Exception:
                _logger.critical("render 线程异常崩溃（固定帧率）", exc_info=True)
                error_msg = (
                    f"{_ANSI_RED}[ChatUI] render 线程异常终止，"
                    f"请联系开发人员查看日志{_ANSI_RESET}\n"
                )
                try:
                    self._renderer.output_adapter.write_raw(error_msg)
                except Exception:
                    sys.__stderr__.write(error_msg)
                    sys.__stderr__.flush()
                self._render_running = False
                break
            elapsed = _time_mod.monotonic() - frame_start
            sleep_time = _FIXED_FRAME_INTERVAL - elapsed
            if sleep_time > 0:
                _time_mod.sleep(sleep_time)
        self._drain_queue_safe()

    def _render_adaptive(self) -> None:
        """自适应等待渲染循环（原有行为）。

        在 daemon 线程中持续运行，循环执行三阶段流水线：
        drain_queue → 自适应等待 → 重复。异常时记录 critical 日志并终止循环。
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
                        idle_count += 1
                        wait_timeout = (
                            _RENDER_INTERVAL
                            if idle_count >= self._IDLE_DRAIN_THRESHOLD
                            else self._ACTIVE_RENDER_INTERVAL
                        )
                    self._cmd_event.wait(timeout=wait_timeout)
                    if not has_content:
                        self._cmd_event.clear()
                except Exception:
                    _logger.critical("render 线程异常崩溃", exc_info=True)
                    error_msg = (
                        f"{_ANSI_RED}[ChatUI] render 线程异常终止，"
                        f"请联系开发人员查看日志{_ANSI_RESET}\n"
                    )
                    try:
                        self._renderer.output_adapter.write_raw(error_msg)
                    except Exception:
                        sys.__stderr__.write(error_msg)
                        sys.__stderr__.flush()
                    self._render_running = False
                    break
        finally:
            self._drain_queue_safe()

    def _drain_queue(self) -> bool:
        """三阶段流水线：预处理面板→获取输出锁→渲染命令→重绘底部栏。

        阶段 1: _phase_pre_update_panels() — 刷新面板回调
        阶段 2: 获取输出锁，批量取出队列中所有命令
        阶段 3: _phase_render() 执行渲染命令，_phase_redraw_bottom() 重绘底部栏

        Returns:
            是否处理了至少一条渲染命令
        """
        commands: list = []
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

    def _position_cursor(self) -> None:
        if not getattr(self._bb, '_active', False):
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
