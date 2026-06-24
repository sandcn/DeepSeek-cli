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
    from ._terminal_io import TerminalIO
    from ._vnode import VNode
    from ._store import TuiStore
    from ._store import TuiState

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
    CmdInputChanged,
)

from ._vnode import diff as _vnode_diff
from ._vnode import PatchKind
_NOOP = PatchKind.NOOP

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
        terminal_io: "TerminalIO | None" = None,
    ):
        self._renderer = renderer
        self._bb = bottom_bar
        self._cursor_tracker = cursor_tracker
        self._tio = terminal_io
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

        # ── VNode Diff 渲染（默认关闭，通过环境变量启用） ──
        self._use_vnode: bool = (
            os.environ.get('CHAT_UI_RENDER_USE_VNODE', '').strip().lower()
            in ('1', 'true', 'yes', 'on')
        )
        self._old_vnode: "VNode | None" = None  # 缓存上帧 VNode 树
        self._store: "TuiStore | None" = None    # VNode 路径专用状态容器
        if self._use_vnode:
            from ._store import TuiStore
            self._store = TuiStore()
            _logger.info("VNode Diff 渲染已启用")

        # ── 可插拔渲染管线（默认关闭，通过环境变量启用） ──
        self._use_phases: bool = (
            os.environ.get('CHAT_UI_RENDER_PHASES', '').strip().lower()
            in ('1', 'true', 'yes', 'on')
        )
        self._phases: list = []
        if self._use_phases:
            from ._render_phase import _DEFAULT_PHASES
            self._phases = list(_DEFAULT_PHASES)
            _logger.info("可插拔渲染管线已启用（%d 个 Phase）", len(self._phases))

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
            # 合并逻辑：CONTENT/REASONING 同类型合并 text；
            # CmdInputChanged 仅保留最新一条（类似 React setState 批处理）
            if isinstance(cmd, (CmdContent, CmdReasoning, CmdInputChanged)):
                try:
                    with self._cmd_queue.mutex:
                        dq = self._cmd_queue.queue
                        if dq and type(dq[-1]) is type(cmd):
                            if isinstance(cmd, CmdInputChanged):
                                # CmdInputChanged：替换为最新值而非合并 text
                                dq[-1] = cmd
                            else:
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

        支持三种路径：
          - VNode Diff 路径（CHAT_UI_RENDER_USE_VNODE=1）：dispatch→store→build→diff→apply patches
          - Rich Live 路径（CHAT_UI_RENDER_USE_RICH_LIVE=1）：内容差分渲染
          - 默认路径：逐条 dispatch 到 TuiRenderer
        """
        try:
            self._bb.sync_bottom_lines()
        except Exception:
            _logger.debug("sync_bottom_lines 异常", exc_info=True)
        self.ensure_cursor_upper()

        if self._use_vnode:
            if self._store is None:
                _logger.warning("CHAT_UI_RENDER_USE_VNODE=1 但 TuiStore 未初始化，回退到直接渲染")
            else:
                # ── VNode Diff 路径 ──
                self._phase_render_vnode(commands)
                return

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

    def _phase_render_vnode(self, commands: list) -> None:
        """VNode Diff 渲染路径。

        1. 逐条 dispatch commands 到 TuiStore
        2. 获取最新 TuiState
        3. 构建新 VNode 树
        4. Diff 新旧树
        5. 若检测到变更，通过 _renderer 渲染所有命令
        6. 缓存新树供下一帧 diff
        """
        try:
            # 1. Dispatch 所有命令到 Store
            for cmd in commands:
                try:
                    self._store.dispatch(cmd)
                except Exception:
                    _logger.debug("VNode dispatch %s 失败", type(cmd).__name__, exc_info=True)

            # 2. 获取最新状态
            state = self._store.get_state()

            # 3. 构建新 VNode 树
            from ._vnode_builder import build_vnode_tree
            new_vnode = build_vnode_tree(state)

            # 4. Diff
            patches = _vnode_diff(self._old_vnode, new_vnode)

            # 5. 若有实质性变更，通过 _renderer 渲染所有命令
            if patches:
                has_change = any(
                    p.kind != _NOOP for p in patches
                    if hasattr(p, 'kind')
                )
                if has_change:
                    for cmd in commands:
                        try:
                            self._renderer.render(cmd)
                        except Exception:
                            _logger.debug("VNode 渲染命令 %s 失败", type(cmd).__name__, exc_info=True)
                    _logger.debug("VNode diff 检测到 %d 个 patches，已触发渲染", len(patches))

            # 6. 缓存新树
            self._old_vnode = new_vnode

        except Exception:
            _logger.warning("VNode Diff 渲染异常，回退到直接渲染", exc_info=True)
            # 回退：逐条直接渲染
            for cmd in commands:
                try:
                    self._renderer.render(cmd)
                except Exception:
                    _logger.debug("回退渲染 %s 失败", type(cmd).__name__, exc_info=True)

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
                self._emit_crash_error()
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
                    self._emit_crash_error()
                    self._render_running = False
                    break
        finally:
            self._drain_queue_safe()

    def _emit_crash_error(self) -> None:
        """输出渲染线程崩溃错误消息到终端（紧急降级路径）。

        优先使用 TerminalIO（写+flush），其次 OutputAdapter，
        最终回退到 sys.__stderr__（写+flush 目标一致）。
        """
        error_msg = (
            f"{_ANSI_RED}[ChatUI] render 线程异常终止，"
            f"请联系开发人员查看日志{_ANSI_RESET}\n"
        )
        if self._tio is not None:
            try:
                self._tio.write_raw(error_msg)
                self._tio.flush()
                return
            except Exception:
                pass
        try:
            self._renderer.output_adapter.write_raw(error_msg)
        except Exception:
            sys.__stderr__.write(error_msg)
            sys.__stderr__.flush()

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
            if self._use_phases and self._phases:
                # ── 可插拔管线路径 ──
                state = self._store.get_state() if self._store is not None else None
                for phase in self._phases:
                    try:
                        phase.execute(self, commands, state)
                    except Exception:
                        _logger.debug("Phase %s 执行异常", type(phase).__name__, exc_info=True)
                # 防御性底部栏重绘：确保即使 phases 列表遗漏 BottomBarPhase/CursorPhase 也能刷新
                from ._render_phase import BottomBarPhase as _BBP
                if not any(isinstance(p, _BBP) for p in self._phases):
                    self._phase_redraw_bottom(has_content)
            else:
                # ── 原有硬编码管线路径 ──
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
        if self._tio is not None:
            self._tio.move_cursor(r_cursor, cursor_col)
            self._tio.flush()
        else:
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
