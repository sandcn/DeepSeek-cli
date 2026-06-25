"""
并行 Agent 显示 — Claude Code 风格（ChatUI 驱动版）

职责分层：
  - ParallelDisplay：生命周期控制 + 状态代理 + 刷新调度
  - FrameRenderer：纯函数渲染（state → 行列表）

渲染路径：ParallelDisplay → push_cmd(RenderCommand.SUBAGENT_FRAME) → 命令队列
  → render 线程出队 → ContentRenderer._do_subagent_frame() → 终端输出。

2026-06-12 重构（渲染路径精简）：
  - 面板帧改为通过 RenderCommand.SUBAGENT_FRAME 命令队列渲染
  - 10Hz fps 状态更新（_phase_pre_update_panels）移到批量出队前执行
  - 帧刷新改为仅由 _panel_refresh_callback() (10Hz Phase 0 定时) 驱动
  - 所有事件驱动路径（add_agent/update_*/tool_* 等）不再触发立即刷新
  - 事件仅写入 AgentStateStore，下一轮 10Hz 心跳自然拾取状态变更
  - 终端缩放（SIGWINCH）设标记 _needs_resize_refresh，由定时回调消费
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...chat_ui.infrastructure.protocol import PanelContext

from ..output_target import IOutputTarget, TerminalTarget
from ..renderer import FrameRenderer
from ..events.event_bus import DisplayEventBus
from ..events.event_types import LiveOutputEvent
from ._config import DisplayConfig
from ..base_display import BaseDisplay
from ..state.agent_state import AgentStateStore
from ..terminal_adapter import (
    register_sigwinch_callback,
    unregister_sigwinch_callback,
)
# 跨层引用说明：CmdSubagentFrame 和 CmdSubagentSlotUpdate 是纯数据 dataclass，
# 属于数据契约层。它们在 chat_ui/commands/types.py 中定义（与所有 Cmd* 类型同文件），
# 保留此 import 作为数据契约引用，避免为两个类型创建独立的共享模块。
from ...chat_ui.commands.types import CmdSubagentFrame, CmdSubagentSlotUpdate

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值
_DEFAULT_HISTORY = 3
_logger = logging.getLogger(__name__)


class _DiffGuard:
    """diff_active 上下文管理器 — 通过 OutputAdapter 直接控制。

    职责：在 diff 输出期间清除并阻止面板渲染，
    输出完成后恢复面板渲染。
    """

    def __init__(self, display: "ParallelDisplay", capture_frame: bool):
        self._display = display
        self._capture_frame = capture_frame

    def __enter__(self):
        d = self._display
        if self._capture_frame and d._last_lines > 0:
            d._clear_frame_lines()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class ParallelDisplay(BaseDisplay):
    """并行 Agent 实时显示管理器 — 命令队列渲染版（代理层）

    职责：
    1. 生命周期控制（start/stop）
    2. 状态更新代理（代理到 AgentStateStore）
    3. 面板刷新调度（通过 RenderCommand.SUBAGENT_FRAME 命令队列渲染）
    4. 特殊输出（capture_and_print/print_output）

    帧渲染通过 RenderCommand 推送到 chat_ui 命令队列，
    由 render 线程的 ContentRenderer._do_subagent_frame() 消费并输出。
    fps 状态更新在 _drain_queue() 的 Phase 0（批量出队前）执行。
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

        # 初始化渲染器
        self._renderer = FrameRenderer(
            terminal_width=self._terminal.terminal_width,
            frame=0,
            max_history=self.max_history,
        )

        # OutputAdapter（由 start() 中从 ChatUIConsumer 获取）
        self._adapter = None

        # ★ push_cmd 回调（由 start() 从 ChatUIConsumer 获取，线程安全）
        self._push_cmd: Any = None

        # 帧状态
        self._frame: int = 0
        self._last_lines: int = 0
        self._last_rendered_version: int = 0
        # DECSTBM 滚动区域底部行号（由 start() 从 chat_ui bottom_bar 获取）
        self._scroll_end: int = 0
        # 缩放刷新标记（信号安全：在 _on_resize 中设置，_panel_refresh_callback 中消费）
        self._needs_resize_refresh: bool = False

        # stdout 捕获锁
        self._capture_lock = asyncio.Lock()

        # PanelContext 注入（替代 get_active_chat_ui() 调用）
        self._panel_ctx: "PanelContext | None" = None
        # claude_style 缓存（由 set_panel_context() 从 PanelContext 获取）
        self._claude_style_enabled: bool = False

    def set_panel_context(self, ctx) -> None:
        """注入 PanelContext（替代 get_active_chat_ui() 调用）。

        由外部调用方（parallel_executor）在 start() 前调用，
        注入 ChatUIConsumer 实例，display.py 通过此协议访问 ChatUI，
        避免直接 import chat_ui 模块。

        同时从 PanelContext 获取 claude_style 开关值并缓存，
        传递给 FrameRenderer，避免 FrameRenderer 直接 import chat_ui 模块。
        """
        self._panel_ctx = ctx
        # 从 PanelContext 获取 claude_style 开关值
        claude_style = getattr(ctx, 'claude_style_enabled', False)
        if callable(claude_style):
            claude_style = claude_style()
        self._claude_style_enabled = bool(claude_style)

        # 用正确的 claude_style 值重建 FrameRenderer
        self._renderer = FrameRenderer(
            terminal_width=self._terminal.terminal_width,
            frame=0,
            max_history=self.max_history,
            claude_style=self._claude_style_enabled,
        )

    # ── 终端缩放回调 ────────────────────────────────────

    def _on_resize(self, width: int, height: int) -> None:
        """终端缩放回调：重建 DisplayConfig + 刷新宽度 + 设置缩放刷新标记。

        信号安全约束（terminal_adapter._handle_sigwinch 禁止获取锁）：
        不在此处调用 _push_frame_cmd() 或任何 I/O/锁操作，
        仅设置标记 _needs_resize_refresh，由 _panel_refresh_callback() 10Hz 安全上下文处理。
        """
        if width <= 0:
            return
        new_config = DisplayConfig(width)
        self.max_history = new_config.max_tool_history_items

        # 刷新渲染器宽度（无锁，直接写简单属性）
        if self._adapter is not None:
            self._adapter.force_refresh_width()

        # ★ 设置缩放刷新标记（信号安全：仅设置布尔值，无锁无 I/O）
        self._needs_resize_refresh = True

    # ── 面板刷新回调（由 render 线程 10Hz Phase 0 调用） ──

    def _panel_refresh_callback(self) -> None:
        """面板刷新回调 — 由 chat_ui render 线程 _phase_pre_update_panels() 10Hz 调用。

        唯一的面板刷新路径：所有事件驱动的 _schedule_refresh() 已改为空操作，
        只有本回调以 10Hz 频率推动面板帧渲染。

        职责：
        1. 消费终端缩放标记（_needs_resize_refresh，由 SIGWINCH → _on_resize 设置）
        2. 更新 scroll_end（终端 resize 自适应）
        3. 推送 SUBAGENT_FRAME 命令到渲染队列（由 Phase 1 批量出队消费）

        注意：不在本回调中直接写终端，所有面板渲染都通过命令队列执行。
        _build_frame() 内部通过版本号检查自动跳过无变更场景。
        """
        if self._adapter is None or self._stopped:
            return

        # 合并 resize 标记消费 + 每帧 scroll_end 刷新，避免重复 import
        reset_last_lines = False
        new_scroll_end: int | None = None

        if self._needs_resize_refresh:
            self._needs_resize_refresh = False
            self._last_rendered_version = 0  # 强制 _build_frame() 跳过版本检查重建帧
            reset_last_lines = True

        try:
            if self._panel_ctx is not None:
                se = self._panel_ctx.bottom_bar.get_scroll_end()
                if se is not None and se > 0:
                    new_scroll_end = int(se)
        except Exception:
            pass

        if reset_last_lines:
            if new_scroll_end is not None:
                self._scroll_end = new_scroll_end
            self._last_lines = 0
        elif new_scroll_end is not None and new_scroll_end != self._scroll_end:
            self._last_lines = 0
            self._scroll_end = new_scroll_end

        # 推送 SUBAGENT_FRAME 命令到渲染队列
        # _build_frame() 通过版本号检查跳过无变更场景，不会产生无效帧
        self._push_frame_cmd()

    # ── diff_active 上下文 ──────────────────────────────

    def _diff_active_guard(self, capture_frame: bool = True):
        """diff_active 上下文管理器 — 清除旧帧并阻止渲染。

        Returns:
            _DiffGuard 实例
        """
        return _DiffGuard(self, capture_frame)

    # ── 注册 ────────────────────────────────────────────

    def add_agent(self, label: str, description: str, status: str = "running",
                  agent_type: str = "plan_execute"):
        self._store.add_agent(label, description, status, agent_type=agent_type)
        self._push_slot_update(label)
        self._schedule_refresh()

    # ── 状态更新（代理到 AgentStateStore） ─────────────

    def update_agent_status(self, label: str, status: str):
        self._store.update_agent_status(label, status)
        self._push_slot_update(label)
        self._schedule_refresh()

    def update_status(self, label: str, status: str):
        return self.update_agent_status(label, status)

    def update_model_phase(self, label: str, phase: str, info: str = ""):
        self._store.update_model_phase(label, phase, info)
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_parsing(self, label: str, tool_name: str, arguments: str = ""):
        self._store.tool_parsing(label, tool_name, arguments)
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_batch_start(self, label: str, tool_names: list):
        self._store.tool_batch_start(label, tool_names)
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_start(self, label: str, tool_name: str, detail: str = "",
                   metadata: dict | None = None):
        self._store.tool_start(label, tool_name, detail)
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_done(self, label: str, tool_name: str = "",
                  success: bool = True, metadata: dict | None = None):
        self._store.tool_done(label, tool_name, success)
        self._push_slot_update(label)
        self._schedule_refresh()

    def update_parse_info(self, label: str, tool_names: str,
                          tokens: int, elapsed: float):
        self._store.update_parse_info(label, tool_names, tokens, elapsed)
        self._push_slot_update(label)
        self._schedule_refresh()

    def parse_info_done(self, label: str) -> None:
        pass

    def update_tokens(self, label: str, tokens: int):
        self._store.update_tokens(label, tokens)
        self._push_slot_update(label)

    def update_usage(self, label: str, usage: dict, replace: bool = False):
        self._store.update_usage(label, usage, replace)
        self._push_slot_update(label)

    def update_live_output(self, label: str, tokens: int):
        self._store.update_live_output(label, tokens)
        self._push_slot_update(label)
        # EventBus 发布去抖
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
        self._push_slot_update(label)

    def update_speed(self, label: str, speed: float):
        self._store.update_speed(label, speed)
        self._push_slot_update(label)

    def set_result(self, label: str, result_text: str = "", error: str = ""):
        self._store.set_result(label, result_text, error)
        self._push_slot_update(label)
        self._schedule_refresh()

    # ── 帧渲染（通过命令队列） ────────────────────────

    def _schedule_refresh(self) -> None:
        """空操作 — 帧刷新由 _panel_refresh_callback() (10Hz 定时) 统一调度。

        保留本方法供外部调用方兼容（add_agent/update_* 等仍可安全调用），
        但不触发任何实际刷新，避免事件驱动的冗余帧推送。
        """

    def _push_slot_update(self, label: str) -> None:
        """将 AgentStateStore 中指定 label 的槽位数据同步到 TuiState。

        从 AgentStateStore 读取最新 slot 快照，转换为可序列化 dict，
        通过 push_cmd 推送 CmdSubagentSlotUpdate 到 chat_ui 命令队列，
        由 TuiStore._reduce_subagent_slot_update 合并到 TuiState.subagent_slots。

        已包含字段：label, description, agent_type, status, start_time, end_time,
            total_calls, input_tokens, output_tokens, live_input_tokens,
            live_output_tokens, last_speed, model_phase, model_info,
            result_text, result_error

        未包含字段：tool_history（待后续支持 — ToolRecord 列表需先设计序列化方案，
            届时需在 CmdSubagentSlotUpdate 中新增 tool_history 字段）。
        """
        if self._push_cmd is None:
            return
        slot = self._store.get_slot(label)
        if slot is None:
            return
        slot_dict = {
            "label": slot.label,
            "description": slot.description,
            "agent_type": slot.agent_type,
            "status": slot.status,
            "start_time": slot.start_time,
            "end_time": slot.end_time,
            "total_calls": slot.total_calls,
            "input_tokens": slot.input_tokens,
            "output_tokens": slot.output_tokens,
            "live_input_tokens": slot.live_input_tokens,
            "live_output_tokens": slot.live_output_tokens,
            "last_speed": slot.last_speed,
            "model_phase": slot.model_phase,
            "model_info": slot.model_info,
            "result_text": slot.result_text,
            "result_error": slot.result_error,
        }
        self._push_cmd(CmdSubagentSlotUpdate(label=label, slot=slot_dict))

    def _build_version_from_tui_state(self) -> dict | None:
        """从 TuiState.subagent_slots 构建帧渲染所需的快照数据（存根）。

        当前返回 None，表示尚未从 TuiState 读取。待 TuiState 补全 tool_history
        字段后，此方法将从 self._panel_ctx 获取 TuiState 并转换为
        FrameRenderer.render() 所需的 (slots_snapshot, order) 格式。

        迁移完成后，_build_frame() 将调用此方法替代 self._store.snapshot_all()。
        """
        return None

    def _build_frame(self, final: bool = False) -> tuple | None:
        """构建面板帧数据（纯函数，不写终端）。

        渲染当前状态到行列表，打包为 (lines, scroll_end, last_lines, clear_eol) 元组，
        供 _push_frame_cmd() 推送到命令队列。

        Args:
            final: 是否结束帧

        Returns:
            (lines, scroll_end, last_lines, clear_eol) 或 None（adapter 缺失时）
        """
        # TODO: 当前仍从 AgentStateStore（self._store）读取快照和顺序，
        # 这是临时过渡方案。计划在 TuiState 补全 tool_history 支持后，
        # 改为从 TuiState.subagent_slots 读取数据，实现完全的状态统一。
        # 届时：slots_snapshot = self._build_version_from_tui_state()
        if self._adapter is None:
            return None

        current_version = self._store.version
        if not final and current_version == self._last_rendered_version:
            return None
        self._last_rendered_version = current_version

        self._frame += 1
        self._renderer.sync_terminal_state(
            width=self._adapter.width,
            frame=self._frame,
        )
        lines = self._renderer.render(
            slots_snapshot=self._store.snapshot_all(),
            order=self._store.get_order(),
            now=time.time(),
            final=final,
        )

        try:
            from .._blessed import get_terminal
            term = get_terminal()
            clear_eol = term.clear_eol if term.clear_eol else "\033[K"
        except Exception:
            clear_eol = "\033[K"

        return (lines, self._scroll_end, self._last_lines, clear_eol)

    def _push_frame_cmd(self) -> None:
        """渲染当前帧并推送 SUBAGENT_FRAME 命令到 chat_ui 渲染队列。

        仅由 _panel_refresh_callback() (10Hz 定时) 调用。
        帧数据在消费侧（ContentRenderer._do_subagent_frame）写入终端。
        """
        packed = self._build_frame()
        if packed is None:
            return
        # 更新 _last_lines 供下次 SU/SD delta 计算
        lines = packed[0]
        self._last_lines = len(lines)
        if self._push_cmd is not None:
            self._push_cmd(CmdSubagentFrame(frame_lines=packed))

    def _clear_frame_lines(self) -> None:
        """清除终端上的帧行。

        有 scroll_end 时使用绝对行号清除，否则降级到旧 sc/rc 行为。
        """
        if self._adapter is None or self._last_lines <= 0:
            return

        # ── 主路径：绝对行号清除 ──
        if self._scroll_end > 0:
            try:
                from .._blessed import get_terminal
                term = get_terminal()
                clear_eol = term.clear_eol if term.clear_eol else "\033[K"
            except Exception:
                clear_eol = "\033[K"

            start = self._scroll_end - self._last_lines + 1
            if start < 1:
                start = 1
            code = ""
            for r in range(start, self._scroll_end + 1):
                code += f"\033[{r};1H{clear_eol}"
            self._adapter.write_raw(code)
            self._last_lines = 0
            return

        # ── 降级路径 ──
        try:
            from .._blessed import get_terminal
            term = get_terminal()
            clear_eol = term.clear_eol if term.clear_eol else "\033[K"
            move_up = term.move_up
            rc = term.rc if term.rc else "\033[u"
        except Exception:
            clear_eol = "\033[K"
            move_up = lambda n: f"\033[{n}A"
            rc = "\033[u"

        code = rc + move_up(self._last_lines)
        for _ in range(self._last_lines):
            code += "\r" + clear_eol + "\n"
        code += move_up(self._last_lines)
        self._adapter.write_raw(code)
        self._last_lines = 0

    # ── 生命周期 ────────────────────────────────────────

    def start(self):
        if self._started:
            return
        self._started = True
        self._stopped = False

        if self._panel_ctx is None:
            return
        _chat_ui = self._panel_ctx
        self._adapter = _chat_ui.output_adapter
        # ★ 获取 push_cmd 回调（向命令队列推送 SUBAGENT_FRAME 命令）
        self._push_cmd = _chat_ui.push_cmd
        # ★ 保存 DECSTBM 滚动区域底部行号，供帧定位使用
        try:
            se = _chat_ui.bottom_bar.get_scroll_end()
            self._scroll_end = int(se) if se is not None else 0
        except Exception:
            self._scroll_end = 0
        # 首次渲染（推送 SUBAGENT_FRAME 命令到队列）
        from .._lock import _try_acquire_output_lock
        with _try_acquire_output_lock(
            name="parallel_display.start", timeout=0.5,
        ) as _locked:
            if _locked:
                _chat_ui.ensure_cursor_upper()
            self._push_frame_cmd()

        # 注册终端 resize 回调
        register_sigwinch_callback(self._on_resize)

        # ★ 注册面板刷新回调到 chat_ui render 线程（10Hz），
        #   替代独立的 500ms 定时器，使 subagent 面板刷新与 render 线程同步。
        try:
            _chat_ui.set_panel_refresh_callback(self._panel_refresh_callback)
        except Exception:
            _logger.debug(
                "注册 panel_refresh_callback 失败（非关键路径，静默跳过）",
            )

    def refresh(self, force: bool = False):
        """公开刷新入口 — 推送 SUBAGENT_FRAME 命令到渲染队列。

        Args:
            force: 是否跳过版本号检查强制渲染（当前忽略，由 _build_frame 内部检查）。
        """
        if self._adapter is not None:
            self._push_frame_cmd()

    # ── 停止 ────────────────────────────────────────────

    def stop(self, final: bool = False) -> None:
        """停止显示（实现 DisplayPort 接口）。

        清除终端上的并行面板。

        Args:
            final: 是否为最终停止
        """
        if self._finished:
            return
        self._finished = True
        self._stopped = True

        # ★ 注销面板刷新回调（render 线程不再调用）
        try:
            if self._panel_ctx is not None:
                self._panel_ctx.set_panel_refresh_callback(None)
        except Exception:
            _logger.debug("注销 panel_refresh_callback 失败", exc_info=True)

        # 注销终端 resize 回调
        unregister_sigwinch_callback(self._on_resize)

        # 清除终端帧
        if self._adapter is not None:
            self._clear_frame_lines()
            self._adapter.flush()
        self._adapter = None

    async def await_stop(self, timeout: float = 2.0):
        """异步停止（兼容旧调用方，委托给 stop）。"""
        self.stop()

    # ── 特殊输出 ───────────────────────────────────────

    def capture_and_print(self, func) -> Any:
        """同步捕获 func 的自定义输出并写入终端。"""
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            result = func()
        diff_text = buf.getvalue()
        if diff_text:
            text = diff_text.rstrip()
            self._terminal.write_line(text)
        return result

    async def capture_and_print_async(self, async_func) -> Any:
        """异步版 capture_and_print。"""
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
        """清除显示帧然后执行 func（func 直接写 stdout）。"""
        with self._diff_active_guard(capture_frame=True):
            return func()

    def print_output(self, text: str):
        """输出文本到终端，清除当前帧并替换。"""
        if not text:
            return
        self._clear_frame_lines()
        self._terminal.write_line(text)
