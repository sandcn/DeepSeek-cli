"""渲染引擎 — TuiEngine + render 线程 + 命令队列。

从 _tui.py 拆分，管理三阶段渲染流水线（预更新面板→获取输出锁→渲染命令→重绘底部栏）。
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from warnings import deprecated
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from ._protocols import BottomBarProtocol
    from ._terminal_io import TerminalIO
    from ._store import TuiStore
    from ._store import TuiState

from ._renderer import TuiRenderer
from ._render_strategy import RenderStrategy, DirectRenderStrategy, VNodeRenderStrategy, PhaseRenderStrategy
from ._const import (
    _RENDER_INTERVAL,
    _DRAIN_LOCK_TIMEOUT,
    _ANSI_RED, _ANSI_RESET,
    _FIXED_FRAME_INTERVAL,
    _ENV_FIXED_FPS,
    _ACTIVE_RENDER_INTERVAL,
    _IDLE_DRAIN_THRESHOLD,
    _CONSECUTIVE_FULL_THRESHOLD,
)

from ._cmd import (
    CmdReasoning,
    CmdContent,
    CmdError,
    CmdInputChanged,
)

from ._lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)


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

        # ── 终端宽度缓存（主副本，供组件层通过 cache 参数使用）──
        self._term_width_cache: dict = {"value": 80, "ts": 0.0}
        # 注入到 _components 模块，使 _get_terminal_width 默认使用此缓存
        import src.chat_ui._components as _comp
        _comp._term_width_cache = self._term_width_cache

        # ── 一次性选择渲染策略（集中读取环境变量，替代分散在渲染循环中的 if/else 分支）──
        self._strategy: RenderStrategy = self._select_strategy()

    def _select_strategy(self) -> RenderStrategy:
        """一次读取所有渲染相关环境变量，选择渲染策略（仅在 __init__ 调用一次）。

        环境变量读取集中于此方法，不再散落在渲染循环中。
        _use_fixed_fps 和 _use_phases 保留为实例属性（供 RenderLoop 使用）。
        Phase 管线优先于 VNode：当 CHAT_UI_RENDER_PHASES=1 时忽略 CHAT_UI_RENDER_USE_VNODE。
        """
        use_vnode: bool = (
            os.environ.get("CHAT_UI_RENDER_USE_VNODE", "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        use_fixed_fps: bool = (
            os.environ.get(_ENV_FIXED_FPS, "").strip().lower()
            in ("1", "true", "yes", "on")
        )
        use_phases: bool = (
            os.environ.get("CHAT_UI_RENDER_PHASES", "").strip().lower()
            in ("1", "true", "yes", "on")
        )

        # 帧率/phase 配置保留为实例属性（供 RenderLoop 使用）
        self._use_fixed_fps = use_fixed_fps
        self._use_phases = use_phases
        if use_fixed_fps:
            _logger.info("固定帧率渲染已启用（%.0f fps）", 1.0 / _FIXED_FRAME_INTERVAL)

        # 选择渲染策略（Phase 管线优先于 VNode）
        if use_phases:
            from ._render_phase import (
                PreUpdatePhase, ContentRenderPhase, BottomBarPhase, CursorPhase
            )
            phases = [
                PreUpdatePhase(),
                ContentRenderPhase(self._renderer),
                BottomBarPhase(),
                CursorPhase(),
            ]
            self._store: "TuiStore | None" = None
            _logger.info("可插拔渲染管线已启用（%d 个 Phase）", len(phases))
            return PhaseRenderStrategy(
                self._renderer, phases, store=self._store
            )
        elif use_vnode:
            from ._store import TuiStore
            from ._vnode_builder import build_vnode_tree
            self._store: "TuiStore | None" = TuiStore()
            _logger.info("VNode Diff 渲染已启用")

            # 创建输出函数：写入 OutputAdapter（通过公开属性而非私有 _adapter）
            def _output_func(text: str) -> None:
                self._renderer.output_adapter.write(text)

            return VNodeRenderStrategy(
                self._renderer, self._store, build_vnode_tree, _output_func,
            )
        else:
            self._store: "TuiStore | None" = None
            return DirectRenderStrategy(self._renderer)

    # ── 向后兼容属性 ──────────────────────────────

    @property
    def _use_vnode(self) -> bool:
        """是否为 VNode 渲染策略（向后兼容，供 _consumer.py / _render_phase.py 查询）。"""
        return isinstance(self._strategy, VNodeRenderStrategy)

    @property
    def _old_vnode(self):
        """缓存的上一帧 VNode 树（向后兼容，供 _render_phase.py 查询）。

        步骤 9（Phase 整合）已完成，此属性保留供 BottomBarPhase VNode 路径使用。
        """
        if isinstance(self._strategy, VNodeRenderStrategy):
            return self._strategy._old_vnode
        return None

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
        start_fn = getattr(self._strategy, "start", None)
        if start_fn is not None:
            try:
                start_fn()
            except Exception:
                _logger.warning("策略 start 失败", exc_info=True)

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
        stop_fn = getattr(self._strategy, "stop", None)
        if stop_fn is not None:
            try:
                stop_fn()
            except Exception:
                _logger.warning("策略 stop 失败", exc_info=True)

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

    @deprecated("步骤 9：Phase 管线已迁移到 PhaseRenderStrategy，此方法不再使用")
    def _phase_render(self, commands: list) -> None:
        """[已废弃] 阶段 2：执行渲染命令。

        步骤 9 后 Phase 管线已整合进 PhaseRenderStrategy，
        ContentRenderPhase 直接使用 TuiRenderer 渲染，不再通过此方法。
        """
        self._strategy.render_commands(self, commands)

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
        """渲染线程主循环（委托给 RenderLoop）。

        异常处理、崩溃通知、运行标志清理和队列排空均在 _render() 层完成，
        RenderLoop 仅负责帧调度，异常时向上传播。
        """
        from ._render_strategy import RenderLoop

        loop = RenderLoop(
            drain_fn=self._drain_queue,
            cmd_event=self._cmd_event,
            get_running=lambda: self._render_running,
            use_fixed_fps=self._use_fixed_fps,
        )
        try:
            loop.run()
        except Exception:
            _logger.critical("render 线程异常崩溃", exc_info=True)
            self._emit_crash_error()
        finally:
            self._render_running = False
            self._drain_queue_safe()

    @deprecated("_render_fixed_fps() 已废弃，请使用 RenderLoop 包装器")
    def _render_fixed_fps(self) -> None:
        """[已废弃] 固定帧率渲染循环（60fps / 16ms）。

        每帧批量 drain 命令队列、执行渲染、重绘底部栏。
        不使用 _cmd_event 信号，完全由固定帧间隔驱动。

        空帧时防御性清理底部栏重绘标记（该标记由 _drain_queue → _phase_redraw_bottom 消费，
        此处仅清理空闲帧的过期标记，避免下一帧误触发冗余重绘）。

        已废弃: 帧调度逻辑已迁移到 RenderLoop 包装器，_render() 方法直接委托给 RenderLoop.run()。
        保留此方法仅供向后兼容，新代码请使用 RenderLoop。
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

    @deprecated("_render_adaptive() 已废弃，请使用 RenderLoop 包装器")
    def _render_adaptive(self) -> None:
        """[已废弃] 自适应等待渲染循环（原有行为）。

        在 daemon 线程中持续运行，循环执行三阶段流水线：
        drain_queue → 自适应等待 → 重复。异常时记录 critical 日志并终止循环。

        已废弃: 帧调度逻辑已迁移到 RenderLoop 包装器，_render() 方法直接委托给 RenderLoop.run()。
        保留此方法仅供向后兼容，新代码请使用 RenderLoop。
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
        阶段 3: 策略统一渲染命令 + _phase_redraw_bottom() 重绘底部栏

        所有渲染策略（Direct / VNode / Phase）统一通过 self._strategy.render_commands()。
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
            if commands:
                has_content = self._strategy.render_commands(self, commands)
            else:
                has_content = False
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
                from ._blessed import get_terminal
                term = get_terminal()
                sys.__stdout__.write(term.move_xy(cursor_col - 1, r_cursor - 1))
            except Exception:
                _logger.debug("position_cursor Blessed 不可用, 使用 ANSI 回退", exc_info=True)
                sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
            sys.__stdout__.flush()
        if self._cursor_tracker is not None:
            self._cursor_tracker.set(r_cursor, cursor_col)


# @deprecated: 使用 TuiEngine 替代。
# 保留仅为测试文件的向后兼容引用（26+ 处）。
RenderEngine: type = TuiEngine  # @deprecated
ContentRenderer: type = TuiRenderer  # @deprecated
