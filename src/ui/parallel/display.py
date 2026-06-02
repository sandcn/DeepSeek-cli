"""
并行 Agent 显示 — Claude Code 风格（ChatUI 驱动版）

职责分层：
  - ParallelDisplay：生命周期控制 + 状态代理 + 刷新调度
  - SubAgentPanelControl：Control 子系统，帧渲染（通过 OutputAdapter）
  - FrameRenderer：纯函数渲染（state → 行列表）

刷新由 ChatUIConsumer 的 _drain_queue 驱动（通过 RenderCommand.SUBAGENT_REFRESH
消息触发 SubAgentPanelControl.render_frame()），替代原 asyncio 定时器周期刷新。
状态更新只写存储不触发现渲染。

2026-06-02 重构：
  - 将帧渲染 I/O 从 ParallelDisplay 迁移到 SubAgentPanelControl（chat_ui 控件体系）
  - diff_active guard 从全局 Event 迁移到 SubAgentPanelControl 实例级标志
  - 刷新路径从直接终端 I/O 改为 RenderCommand.SUBAGENT_REFRESH 消息驱动
  - 全局引用 _active_parallel_display → _active_subagent_panel
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
from ..terminal_adapter import register_sigwinch_callback, unregister_sigwinch_callback

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值，防止高频 update 路径过度发布
_DEFAULT_HISTORY = 3
_logger = logging.getLogger(__name__)


class _DiffGuard:
    """diff_active 上下文管理器 — 通过 SubAgentPanelControl 控制。

    职责：在 diff 输出期间设置 panel.diff_active_set() 阻止面板渲染，
    输出完成后调用 panel.diff_active_clear() 恢复面板渲染。
    不主动触发渲染——由 ChatUI _drain_queue 在下一跳自然恢复。
    """

    def __init__(self, display: "ParallelDisplay", capture_frame: bool):
        self._display = display
        self._capture_frame = capture_frame

    def __enter__(self):
        d = self._display
        panel = d._panel

        if panel is not None:
            # 阶段1：快照帧行数
            last_lines_snapshot = panel.last_lines

            # 阶段2：设置 diff_active（引用计数，单事件循环天然原子）
            panel.diff_active_set()

            # 阶段3：清除旧帧（通过 OutputAdapter.write_raw 走 output_lock 路径）
            if self._capture_frame and last_lines_snapshot > 0:
                from ..ui.terminal_adapter import TerminalAdapter
                code = TerminalAdapter.clear_lines_code(last_lines_snapshot)
                if code and panel._adapter is not None:
                    panel._adapter.write_raw(code)
                panel.last_lines = 0

    def __exit__(self, exc_type, exc_val, exc_tb):
        d = self._display
        panel = d._panel

        if panel is not None:
            panel.diff_active_clear()

        return False


class ParallelDisplay(BaseDisplay):
    """并行 Agent 实时显示管理器 — ChatUI 驱动版（代理层）

    职责：
    1. 生命周期控制（start/stop）
    2. 状态更新代理（代理到 AgentStateStore）
    3. 刷新调度（通过 RenderCommand.SUBAGENT_REFRESH 消息驱动 SubAgentPanelControl）
    4. 特殊输出（capture_and_print/print_output）

    帧渲染委托给 SubAgentPanelControl（Control 体系），
    状态管理委托给 AgentStateStore。
    刷新由 ChatUIConsumer 的 reader 线程在 _drain_queue 中触发，
    通过 RenderCommand.SUBAGENT_REFRESH 消息路径与 ChatUI 渲染命令串行化处理。
    """

    def __init__(self, max_history: int = _DEFAULT_HISTORY,
                 output_target: IOutputTarget | None = None):
        super().__init__(output_target=output_target)
        self._store = AgentStateStore()
        self._terminal = output_target or TerminalTarget()
        self._started = False
        self._finished = False
        self._stopped = False
        self._last_eventbus_time: float = 0.0  # EventBus 上次发布时间戳

        # 根据终端宽度确定显示深度
        display_config = DisplayConfig(self._terminal.terminal_width)
        self.max_history = max_history or display_config.max_tool_history_items

        # 初始化渲染器（终端状态在每帧渲染前同步）
        self._renderer = FrameRenderer(
            terminal_width=self._terminal.terminal_width,
            frame=0,
            max_history=self.max_history,
        )

        # SubAgentPanelControl — 在 start() 中创建
        self._panel: "SubAgentPanelControl | None" = None

        # stdout 捕获锁：串行化 capture_and_print_async 的 redirect_stdout 访问，
        # 防止多协程并发时 save/restore 模式被协程交错破坏（输出丢失/泄漏）
        self._capture_lock = asyncio.Lock()

        # ★ 延迟导入 SubAgentPanelControl，避免循环引用
        from src.chat_ui._controls import SubAgentPanelControl  # noqa: PLC0415
        self._SubAgentPanelControl = SubAgentPanelControl

    # ── 终端缩放回调 ────────────────────────────────────

    def _on_resize(self, width: int, height: int) -> None:
        """终端缩放回调：重建 DisplayConfig + 入队 SUBAGENT_REFRESH 刷新面板。"""
        if width <= 0:
            return
        new_config = DisplayConfig(width)
        self.max_history = new_config.max_tool_history_items

        # 更新 renderer 并刷新
        if self._panel is not None:
            self._panel.refresh_width()
        self._push_refresh()

    # ── diff_active 上下文 ──────────────────────────────

    def _diff_active_guard(self, capture_frame: bool = True):
        """diff_active 上下文管理器 — 设置/清除 + 引用计数 + 超时保护。

        单事件循环设计：引用计数在单线程中天然原子，无需额外锁保护。

        Returns:
            _DiffGuard 实例
        """
        return _DiffGuard(self, capture_frame)

    # ── 注册 ────────────────────────────────────────────

    def add_agent(self, label: str, description: str, status: str = "running",
                  agent_type: str = "ordinary"):
        self._store.add_agent(label, description, status, agent_type=agent_type)

    # ── 状态更新（代理到 AgentStateStore） ─────────────

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
    # 注册 SubAgentPanelControl 到 chat_ui._state._active_subagent_panel，
    # 由 ChatUIConsumer._drain_queue 通过 RenderCommand.SUBAGENT_REFRESH
    # 消息驱动帧刷新。

    def start(self):
        if self._started:
            return
        self._started = True
        self._stopped = False

        import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
        _chat_ui = _chat_ui_mod.get_active_chat_ui()
        if _chat_ui is not None:
            # ★ 获取 OutputAdapter（通过 ChatUIConsumer 注入）
            adapter = _chat_ui.output_adapter

            # ★ 创建 SubAgentPanelControl
            self._panel = self._SubAgentPanelControl(
                output_adapter=adapter,
                store=self._store,
                renderer=self._renderer,
            )

            # ★ 首次渲染前确保光标在上屏区域（内容区），防止面板首次渲
            #   染时光标位于下屏（输入区），导致面板内容先渲染到输入区
            #   再被后续 _drain_queue 的 refresh 修正到上屏的闪烁问题。
            from .._lock import _try_acquire_output_lock
            with _try_acquire_output_lock(
                name="parallel_display.start", timeout=0.5,
            ) as _locked:
                if _locked:
                    _chat_ui.ensure_cursor_upper()
                    self._panel.render_frame()
                else:
                    # 锁超时降级：不持锁直接渲染（与修改前行为一致）
                    self._panel.render_frame()
        else:
            # ChatUI 未激活，无底部栏分屏，无需关心光标位置
            # 仍创建 panel 但无 OutputAdapter — 帧渲染将 no-op
            self._panel = None

        # ★ 首帧渲染完成后注册 SubAgentPanelControl，Reader 线程 Phase 2 从此开始接管
        if self._panel is not None:
            _chat_ui_mod._state._active_subagent_panel = self._panel

        # ★ 注册终端 resize 回调
        register_sigwinch_callback(self._on_resize)

    def refresh(self, force: bool = False):
        """公开刷新入口 — 由 ChatUIConsumer._drain_queue 触发。

        通过 RenderCommand.SUBAGENT_REFRESH 消息路径驱动帧刷新，
        统一到 ChatUI 命令队列中串行化处理。

        Args:
            force: 是否跳过版本号检查强制渲染。
        """
        self._push_refresh()

    def _push_refresh(self) -> None:
        """入队 SUBAGENT_REFRESH 命令到活跃 ChatUI 的引擎队列。"""
        import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
        _chat_ui = _chat_ui_mod.get_active_chat_ui()
        if _chat_ui is not None:
            try:
                _chat_ui._engine.push_cmd(
                    (_chat_ui_mod.RenderCommand.SUBAGENT_REFRESH, False)
                )
            except Exception:
                _logger.debug("_push_refresh: 入队 SUBAGENT_REFRESH 失败", exc_info=True)

    # ── 停止 ────────────────────────────────────────────

    def stop(self, final: bool = False) -> None:
        """停止显示（实现 DisplayPort 接口）。

        清除终端上的并行面板，关闭 SubAgentPanelControl。
        确保后续 subagent 结果输出不会与旧帧行重叠。

        Args:
            final: 是否为最终停止（兼容 EventBus 的 SessionStopped 事件）
        """
        if self._finished:
            return
        self._finished = True
        self._stopped = True

        # 从 ChatUI 注销全局引用
        import src.chat_ui as _chat_ui_mod  # noqa: PLC0415
        if (self._panel is not None
                and _chat_ui_mod._state._active_subagent_panel is self._panel):
            _chat_ui_mod._state._active_subagent_panel = None

        # 注销终端 resize 回调
        unregister_sigwinch_callback(self._on_resize)

        # ★ 关闭 SubAgentPanelControl（清除终端帧 + 标记关闭）
        if self._panel is not None:
            self._panel.close()
            self._panel = None

    async def await_stop(self, timeout: float = 2.0):
        """异步停止（兼容旧调用方，委托给 stop）。"""
        self.stop()

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

        通过 SubAgentPanelControl.clear_frame() 清除帧行，
        然后通过 TerminalTarget.write_line() 写入新文本。
        """
        if not text:
            return
        if self._panel is not None:
            self._panel.clear_frame()
        self._terminal.write_line(text)
