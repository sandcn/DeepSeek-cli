"""
并行 Agent 显示 — Claude Code 风格（ChatUI 驱动版）

职责分层：
  - ParallelDisplay：生命周期控制 + 状态代理 + 刷新调度
  - FrameRenderer：纯函数渲染（state → 行列表）
  - TerminalAdapter：终端 I/O 抽象

刷新由 ChatUIConsumer 的 _drain_queue 驱动（调用 refresh() → _render_frame_unlocked()），
替代原 asyncio 定时器周期刷新。状态更新只写存储不触发现渲染。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..output_target import IOutputTarget, TerminalTarget
from ..renderer import FrameRenderer
from ..events.event_bus import DisplayEventBus
from ..events.event_types import LiveOutputEvent
from ._config import DisplayConfig
from ..base_display import BaseDisplay
from ..common.state_store import AgentStateStore
from .._lock import diff_active, _try_acquire_output_lock
from ..terminal_adapter import register_sigwinch_callback, unregister_sigwinch_callback

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值，防止高频 update 路径过度发布
_DEFAULT_HISTORY = 3
_logger = logging.getLogger(__name__)


class _DiffGuard:
    """diff_active 上下文管理器。

    职责：在 diff 输出期间设置 diff_active 阻止面板渲染，
    输出完成后恢复面板渲染。不主动触发渲染，
    由 ChatUI _drain_queue 在下一跳自然恢复。
    """

    def __init__(self, display: "ParallelDisplay", capture_frame: bool):
        self._display = display
        self._capture_frame = capture_frame

    def __enter__(self):
        d = self._display

        # 阶段1：快照帧行数
        last_lines_snapshot = d._last_lines

        # 阶段2：设置 diff_active（引用计数，单线程天然原子）
        d._diff_count += 1
        if d._diff_count == 1:
            diff_active.set()
            d._diff_active_since = time.time()

        # 阶段3：清除旧帧（I/O 操作，用 output_lock 保护）
        if self._capture_frame:
            clr = d._terminal.clear_lines_code(last_lines_snapshot)
            if clr:
                with _try_acquire_output_lock(name="_DiffGuard.clear_frame"):
                    d._terminal.write(clr)
            d._last_lines = 0

    def __exit__(self, exc_type, exc_val, exc_tb):
        d = self._display

        # 清除 diff_active
        d._diff_count -= 1
        if d._diff_count == 0:
            diff_active.clear()

        return False


class ParallelDisplay(BaseDisplay):
    """并行 Agent 实时显示管理器 — ChatUI 驱动版

    职责：
    1. 生命周期控制（start/stop）
    2. 状态更新代理（代理到 AgentStateStore）
    3. 刷新调度（由 ChatUIConsumer._drain_queue 驱动 refresh()）
    4. 特殊输出（capture_and_print/print_output）

    渲染逻辑委托给 FrameRenderer，终端 I/O 委托给 TerminalAdapter。
    刷新由 ChatUIConsumer 的 reader 线程在 _drain_queue 中触发，
    与 ChatUI 自身渲染命令在同一循环中串行化处理。
    """

    def __init__(self, max_history: int = _DEFAULT_HISTORY,
                 output_target: IOutputTarget | None = None):
        super().__init__(output_target=output_target)
        self._store = AgentStateStore()
        self._terminal = output_target or TerminalTarget()
        self._frame = 0
        self._last_lines = 0
        self._started = False
        self._finished = False
        self._stopped = False
        self._diff_count = 0
        self._diff_active_since = 0.0
        self._last_eventbus_time: float = 0.0  # EventBus 上次发布时间戳
        self._last_rendered_version: int = 0   # 上次渲染时的 store 版本号

        # 根据终端宽度确定显示深度
        display_config = DisplayConfig(self._terminal.terminal_width)
        self.max_history = max_history or display_config.max_tool_history_items

        # 初始化渲染器（终端状态在每帧渲染前同步）
        self._renderer = FrameRenderer(
            terminal_width=self._terminal.terminal_width,
            frame=self._frame,
            max_history=self.max_history,
        )

        # stdout 捕获锁：串行化 capture_and_print_async 的 redirect_stdout 访问，
        # 防止多协程并发时 save/restore 模式被协程交错破坏（输出丢失/泄漏）
        self._capture_lock = asyncio.Lock()

    # ── 终端缩放回调 ────────────────────────────────────

    def _on_resize(self, width: int, height: int) -> None:
        """终端缩放回调：重建 DisplayConfig + 更新 renderer + 主动刷新面板。"""
        if width <= 0:
            return
        new_config = DisplayConfig(width)
        self.max_history = new_config.max_tool_history_items
        self.refresh()  # ★ B6 fix: resize 后主动刷新面板，无需等待下次 state 变化

    # ── diff_active 上下文 ──────────────────────────────

    def _diff_active_guard(self, capture_frame: bool = True):
        """diff_active 上下文管理器 — 设置/清除 + 引用计数 + 超时保护。

        单事件循环设计：_diff_count 引用计数在单线程中天然原子，
        无需额外锁保护。

        Returns:
            _DiffGuard 实例
        """
        return _DiffGuard(self, capture_frame)

    # ── 注册 ────────────────────────────────────────────

    def add_agent(self, label: str, description: str, status: str = "running",
                  agent_type: str = "ordinary"):
        self._store.add_agent(label, description, status, agent_type=agent_type)

    # ── 状态更新 ────────────────────────────────────────

    def update_agent_status(self, label: str, status: str):
        self._store.update_agent_status(label, status)

    def update_status(self, label: str, status: str):
        return self.update_agent_status(label, status)

    def update_model_phase(self, label: str, phase: str, info: str = ""):
        self._store.update_model_phase(label, phase, info)

    def tool_parsing(self, label: str, tool_name: str, arguments: str = ""):
        self._store.tool_parsing(label, tool_name, arguments)

    def tool_batch_start(self, label: str, tool_names: list):
        self._store.tool_batch_start(label, tool_names)

    def tool_start(self, label: str, tool_name: str, detail: str = "",
                   metadata: dict | None = None):
        self._store.tool_start(label, tool_name, detail)

    def tool_done(self, label: str, tool_name: str = "",
                  success: bool = True, metadata: dict | None = None):
        self._store.tool_done(label, tool_name, success)

    def update_parse_info(self, label: str, tool_names: str,
                          tokens: int, elapsed: float):
        self._store.update_parse_info(label, tool_names, tokens, elapsed)

    def parse_info_done(self, label: str) -> None:
        pass

    def update_tokens(self, label: str, tokens: int):
        self._store.update_tokens(label, tokens)

    def update_usage(self, label: str, usage: dict, replace: bool = False):
        self._store.update_usage(label, usage, replace)

    def update_live_output(self, label: str, tokens: int):
        self._store.update_live_output(label, tokens)
        # EventBus 发布去抖：_EVENTBUS_THROTTLE 窗口内只发一次，避免高频路径过度发布
        now = time.time()
        if now - self._last_eventbus_time >= _EVENTBUS_THROTTLE:
            self._last_eventbus_time = now
            try:
                DisplayEventBus.get_default().publish(LiveOutputEvent(
                    label=label, tokens=tokens, source=label,
                ))
            except Exception:
                _logger.debug("EventBus 发布 LiveOutputEvent 失败（非关键路径，忽略）")

    def update_live_input(self, label: str, tokens: int):
        self._store.update_live_input(label, tokens)

    def update_speed(self, label: str, speed: float):
        self._store.update_speed(label, speed)

    def set_result(self, label: str, result_text: str = "", error: str = ""):
        self._store.set_result(label, result_text, error)

    # ── 刷新调度（ChatUI 驱动） ──────────────────────
    #
    # 不再使用独立 asyncio 定时器周期刷新。
    # 注册到全局 _active_parallel_display，由 ChatUIConsumer
    # 的 _drain_queue 在每次渲染循环中调用 refresh() 触发帧刷新。

    def start(self):
        if self._started:
            return
        self._started = True
        self._stopped = False

        # ★ 先不注册 _active_parallel_display（推迟到首帧渲染完成后），
        #   防止 Reader 线程 _drain_queue Phase 2 在首次渲染前检测到
        #   活跃 display 并触发 pd.refresh() → _render_frame_unlocked()
        #   （此时光标在输入区、last_lines=0 无 \033[u 恢复），渲染
        #   错误位置的首帧。

        import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
        _chat_ui = _chat_ui_mod.get_active_chat_ui()
        if _chat_ui is not None:
            # ★ 首次渲染前确保光标在上屏区域（内容区），防止面板首次渲
            #   染时光标位于下屏（输入区），导致面板内容先渲染到输入区
            #   再被后续 _drain_queue 的 refresh 修正到上屏的闪烁问题。
            #   原因：render_frame 首次调用（last_lines=0）不从已保存的
            #   SCOSC 恢复光标位置，而是从当前位置开始写入。
            with _try_acquire_output_lock(
                name="parallel_display.start", timeout=0.5,
            ) as _locked:
                if _locked:
                    # ★ 锁内完成光标定位 + 首次渲染，消除 Reader 线程
                    #   _position_cursor()（_drain_queue Phase 3 不持锁
                    #   路径）在两者之间插入移回输入区的竞态窗口。
                    #   output_lock 为 RLock，render_frame 内部
                    #   _try_acquire_output_lock 可重入获取，不会死锁。
                    _chat_ui.ensure_cursor_upper()
                    self._render_frame_unlocked()
                else:
                    # 锁超时降级：不持锁直接渲染（与修改前行为一致）
                    self._render_frame_unlocked()
        else:
            # ChatUI 未激活，无底部栏分屏，无需关心光标位置
            self._render_frame_unlocked()

        # ★ 首帧渲染完成后再注册，Reader 线程 Phase 2 从此开始接管
        # ★ 通过 _state 模块写入，与 _engine.py/_consumer.py 的读取路径一致。
        #   不可用 _chat_ui_mod._active_parallel_display = self 写入，
        #   因为 __init__.py 的 from ._state import _active_parallel_display
        #   创建了独立绑定，赋值后与 _state 模块的变量分叉，导致 Reader
        #   线程中的 pd 永远为 None，面板永不刷新。
        _chat_ui_mod._state._active_parallel_display = self
        # ★ B6 fix: 注册终端 resize 回调，resize 后主动刷新面板
        register_sigwinch_callback(self._on_resize)

    def refresh(self, force: bool = False):
        """公开刷新入口 — 由 ChatUIConsumer._drain_queue 在每次渲染循环中调用。

        内部 _render_frame_unlocked 自行管理终端 I/O 同步（try-lock 超时保护）。
        可在持 output_lock 状态下安全调用（output_lock 为 RLock，可重入）。
        渲染异常被内部捕获并记录日志，不会向上传播。

        Args:
            force: 是否跳过版本号检查强制渲染。当处理完 SubAgent 相关
                   渲染命令（TOOL_OUTPUT 等）时应传入 True，确保面板
                   及时更新展示最新子代理状态。
        """
        self._render_frame_unlocked(force=force)

    # ── 停止 ────────────────────────────────────────────

    def stop(self, final: bool = False) -> None:
        """停止显示（实现 DisplayPort 接口）。

        清除终端上的并行面板，归零帧行数。
        确保后续 subagent 结果输出（_stream_results_markdown）不会与
        旧帧行重叠。

        Args:
            final: 是否为最终停止（兼容 EventBus 的 SessionStopped 事件）
        """
        if self._finished:
            return
        self._finished = True
        self._stopped = True

        # 从 ChatUI 注销全局引用
        import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
        if _chat_ui_mod._state._active_parallel_display is self:
            _chat_ui_mod._state._active_parallel_display = None

        # 注销终端 resize 回调
        unregister_sigwinch_callback(self._on_resize)

        # 清理全局 diff_active 状态
        self._cleanup_diff_active()

        # ★ 清除终端上的旧帧行，防止后续 subagent 结果 markdown
        #   文本与残留的并行面板行重叠。
        last_lines = self._last_lines
        self._last_lines = 0
        if last_lines > 0:
            buf = self._terminal.clear_lines_code(last_lines)
            if buf:
                self._terminal.write(buf)

    async def await_stop(self, timeout: float = 2.0):
        """异步停止（兼容旧调用方，委托给 stop）。"""
        self.stop()

    def _cleanup_diff_active(self) -> None:
        """清理全局 diff_active 事件和内部 _diff_count 计数器。"""
        was_active = diff_active.is_set()
        if was_active or self._diff_count != 0:
            if was_active:
                _logger.warning(
                    "stop 清理残留的 diff_active（_diff_count=%d）",
                    self._diff_count,
                )
            diff_active.clear()
            self._diff_count = 0
            self._diff_active_since = 0.0

    # ── 特殊输出 ───────────────────────────────────────

    def capture_and_print(self, func) -> Any:
        """同步捕获 func 的自定义输出并写入终端。

        调用方应在 _diff_active_guard 上下文内调用此方法，
        或确保 diff_active 已置位，避免面板渲染与输出交错。
        """
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            result = func()
        diff_text = buf.getvalue()
        if diff_text:
            text = diff_text.rstrip()
            # diff_active 已设置 → 渲染被跳过
            # TerminalTarget.write_line() 内部自管理 output_lock
            self._terminal.write_line(text)
        return result

    async def capture_and_print_async(self, async_func) -> Any:
        """异步版 capture_and_print，用于 subagent 的 func.display() 调用。

        单事件循环设计：_DiffGuard 的引用计数天然原子，
        无需锁保护 diff_active 状态。
        output_lock 由 TerminalTarget.write_line() 内部自行管理（带超时）。
        """
        from io import StringIO
        import contextlib

        async def _run():
            buf = StringIO()
            async with self._capture_lock:
                with contextlib.redirect_stdout(buf):
                    result = await async_func()
            diff_text = buf.getvalue()
            if diff_text:
                text = diff_text.rstrip()
                self._terminal.write_line(text)
            return result

        with self._diff_active_guard(capture_frame=True):
            return await _run()

    def clear_frame_and_run(self, func) -> Any:
        """清除显示帧 + 设置 diff_active，然后执行 func（func 直接写 stdout）。

        与 _diff_active_guard 配合，确保 func 的输出不与面板渲染交错。
        单事件循环设计，无需锁保护。
        """
        with self._diff_active_guard(capture_frame=True):
            return func()

    def print_output(self, text: str):
        """输出文本到终端，清除当前帧并替换。

        两步操作（无锁）：
        1. 快照 _last_lines 并重置
        2. 写终端（output_lock 由 TerminalTarget 内部管理）
        """
        if not text:
            return
        last_lines = self._last_lines
        self._last_lines = 0
        if last_lines > 0:
            buf = self._terminal.clear_lines_code(last_lines)
            if buf:
                self._terminal.write(buf)
        self._terminal.write_line(text)

    # ── 渲染 ───────────────────────────────────────────

    def _render_frame_unlocked(self, final: bool = False, force: bool = False):
        # ── diff_active 超时兜底 ──────────────────────────────
        # capture_and_print_async / clear_frame_and_run 在异常或取消时
        # 可能未正确清除 diff_active，导致渲染被永久跳过。
        # 超过阈值后强制清除，恢复帧刷新。
        _DIFF_ACTIVE_TIMEOUT = 30.0
        if diff_active.is_set() and not final:
            since = self._diff_active_since
            elapsed = time.time() - since if since > 0 else 0.0
            if since > 0 and elapsed > _DIFF_ACTIVE_TIMEOUT:
                _logger.warning(
                    "diff_active 超过 %ds 未清除（_diff_count=%d），强制清除",
                    _DIFF_ACTIVE_TIMEOUT, self._diff_count,
                )
                diff_active.clear()
                self._diff_active_since = 0.0
                self._diff_count = 0
            else:
                # diff 渲染中 → 跳过帧刷新
                self._last_lines = 0
                return

        # stopped 且非最终帧 → 跳过渲染
        if self._stopped and not final:
            return

        # ★ 版本跳过：state 未变化时跳过帧渲染（ioctl+render 开销较大）
        #   force=True 时跳过此检查——由 render() 函数在处理 SubAgent
        #   相关命令后主动调用，确保子代理面板及时刷新展示最新状态。
        current_version = self._store.version
        if not final and not force and current_version == self._last_rendered_version:
            return
        self._last_rendered_version = current_version

        # 帧计数：通过所有跳过检查后递增，避免空增
        self._frame += 1

        try:
            # 同步最新终端状态到渲染器
            self._renderer.sync_terminal_state(
                width=self._terminal.terminal_width,
                frame=self._frame,
            )

            lines = self._renderer.render(
                slots_snapshot=self._store.snapshot_all(),
                order=self._store.get_order(),
                now=time.time(),
                final=final,
            )

            # 不持 output_lock：单事件循环模式无并发渲染线程，
            # diff_active 保证帧渲染与 print 输出互斥
            self._last_lines = self._terminal.render_frame(lines, self._last_lines)
        except Exception:
            _logger.exception("_render_frame_unlocked 渲染异常，跳过本帧")
