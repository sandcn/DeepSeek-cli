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
import functools
import logging
import time
import warnings
from typing import Any


def deprecated(message: str):
    """「废弃标记」装饰器 — Python 3.13+ warnings.deprecated 的向下兼容实现。

    Python 3.13 (PEP 702) 在 warnings 模块中新增了 @deprecated 装饰器。
    本实现为 Python 3.9 提供等价行为：调用被装饰函数时发出 DeprecationWarning。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            warnings.warn(
                f"{func.__name__} is deprecated: {message}",
                DeprecationWarning,
                stacklevel=2,
            )
            return func(*args, **kwargs)
        return wrapper
    return decorator

from .config import TuiConfig
from .core.output_target import IOutputTarget, TerminalTarget
from .frame import FrameRenderer
from .events.event_bus import DisplayEventBus
from .events.event_types import MetricsUpdateEvent
from .core.parallel_config import DisplayConfig
from .consumer.base_display import BaseDisplay
from .state.agent_state import AgentStateStore
from .state.consumer_registry import get_active_chat_ui
from .terminal.adapter import (
    register_sigwinch_callback,
    unregister_sigwinch_callback,
)
from .engine.const import RenderCommand

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
        self._eventbus_throttle: float = TuiConfig.defaults().eventbus_throttle  # 从配置读取

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
        # 上次帧渲染时间戳（用于定时强制刷新，保持 spinner 动画）
        self._last_frame_time: float = 0.0

        # stdout 捕获锁
        self._capture_lock = asyncio.Lock()

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
            _chat_ui = get_active_chat_ui()
            if _chat_ui is not None:
                se = _chat_ui.bottom_bar.get_scroll_end()
                if se is not None and se > 0:
                    new_scroll_end = int(se)
        except Exception as exc:
            _logger.debug("_panel_refresh_callback: 获取 chat_ui 失败: %s", exc)

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
                  agent_type: str = "execute"):
        self._store.add_agent(label, description, status, agent_type=agent_type)
        self._schedule_refresh()

    # ── 状态更新（代理到 AgentStateStore） ─────────────

    def update_agent_status(self, label: str, status: str):
        self._store.update_agent_status(label, status)
        self._schedule_refresh()

    def update_status(self, label: str, status: str):
        return self.update_agent_status(label, status)

    def update_model_phase(self, label: str, phase: str, info: str = ""):
        self._store.update_model_phase(label, phase, info)
        self._schedule_refresh()

    def tool_parsing(self, label: str, tool_name: str, arguments: str = ""):
        self._store.tool_parsing(label, tool_name, arguments)
        self._schedule_refresh()

    def tool_batch_start(self, label: str, tool_names: list):
        self._store.tool_batch_start(label, tool_names)
        self._schedule_refresh()

    def tool_start(self, label: str, tool_name: str, detail: str = "",
                   metadata: dict | None = None):
        self._store.tool_start(label, tool_name, detail)
        self._schedule_refresh()

    def tool_done(self, label: str, tool_name: str = "",
                  success: bool = True, metadata: dict | None = None):
        self._store.tool_done(label, tool_name, success)
        self._schedule_refresh()

    def update_parse_info(self, label: str, tool_names: str,
                          tokens: int, elapsed: float):
        self._store.update_parse_info(label, tool_names, tokens, elapsed)
        self._schedule_refresh()

    @deprecated("不再需要，由 10Hz 定时回调驱动帧刷新替代")
    def parse_info_done(self, label: str) -> None:
        """空操作 — 帧刷新已由 _panel_refresh_callback() (10Hz 定时) 统一调度。

        保留本方法供外部调用方兼容，不再触发实际刷新。
        """
        import warnings
        warnings.warn("parse_info_done is deprecated: 帧刷新由 10Hz 定时回调驱动", DeprecationWarning, stacklevel=2)
        _logger.debug("parse_info_done called for %s", label)

    def update_tokens(self, label: str, tokens: int):
        self._store.update_tokens(label, tokens)

    def update_usage(self, label: str, usage: dict, replace: bool = False):
        self._store.update_usage(label, usage, replace)

    def update_live_output(self, label: str, tokens: int):
        self._store.update_live_output(label, tokens)
        # EventBus 发布去抖（从 TuiConfig.eventbus_throttle 读取阈值）
        now = time.time()
        if now - self._last_eventbus_time >= self._eventbus_throttle:
            self._last_eventbus_time = now
            try:
                DisplayEventBus.get_default().publish(MetricsUpdateEvent(
                    label=label, live_output_tokens=tokens, source=label,
                ))
            except Exception as exc:
                _logger.debug("EventBus 发布 MetricsUpdateEvent 失败（非关键路径，忽略）: %s", exc)

    def update_live_input(self, label: str, tokens: int):
        self._store.update_live_input(label, tokens)

    def update_speed(self, label: str, speed: float):
        self._store.update_speed(label, speed)

    def set_result(self, label: str, result_text: str = "", error: str = ""):
        self._store.set_result(label, result_text, error)
        self._schedule_refresh()

    # ── 帧渲染（通过命令队列） ────────────────────────

    @deprecated("不再需要，帧刷新由 10Hz 定时回调驱动")
    def _schedule_refresh(self) -> None:
        """空操作 — 帧刷新由 _panel_refresh_callback() (10Hz 定时) 统一调度。

        保留本方法供外部调用方兼容（add_agent/update_* 等仍可安全调用），
        但不触发任何实际刷新，避免事件驱动的冗余帧推送。
        """
        import warnings
        warnings.warn("_schedule_refresh is deprecated: 帧刷新由 10Hz 定时回调驱动", DeprecationWarning, stacklevel=2)

    def _build_frame(self, final: bool = False) -> tuple | None:
        """构建面板帧数据（纯函数，不写终端）。

        渲染当前状态到行列表，打包为 (lines, scroll_end, last_lines, clear_eol) 元组，
        供 _push_frame_cmd() 推送到命令队列。

        Args:
            final: 是否结束帧

        Returns:
            (lines, scroll_end, last_lines, clear_eol) 或 None（adapter 缺失时）
        """
        if self._adapter is None:
            return None

        current_version = self._store.version
        now_local = time.monotonic()
        if not final and current_version == self._last_rendered_version:
            # 版本号未变时：如果距上次渲染超过 80ms，强制刷新以保持 spinner 动画
            if now_local - self._last_frame_time < 0.08:
                return None
        else:
            self._last_rendered_version = current_version
        self._last_frame_time = now_local

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
            from .terminal.blessed import get_terminal
            term = get_terminal()
            clear_eol = term.clear_eol if term.clear_eol else "\033[K"
        except Exception as exc:
            _logger.debug("_build_frame: 获取 clear_eol 失败，回退 \\033[K: %s", exc)
            clear_eol = "\033[K"

        return (lines, self._scroll_end, self._last_lines, clear_eol)

    def _push_frame_cmd(self) -> None:
        """渲染当前帧并推送 SUBAGENT_FRAME 命令到 chat_ui 渲染队列。

        仅由 _panel_refresh_callback() (10Hz 定时) 调用。
        帧数据在消费侧（ContentRenderer._do_subagent_frame）写入终端。

        若 push 失败（命令队列异常），重置 _last_rendered_version 为 0，
        强制下一帧重建，避免帧丢失（_build_frame 版本号检查会因版本
        未变 + 80ms 内而跳过重建，导致失败帧永不被重试）。
        """
        packed = self._build_frame()
        if packed is None:
            return
        # 更新 _last_lines 供下次 SU/SD delta 计算
        lines = packed[0]
        self._last_lines = len(lines)
        if self._push_cmd is not None:
            try:
                self._push_cmd((RenderCommand.SUBAGENT_FRAME, packed))
            except Exception as exc:
                _logger.debug("_push_cmd 推送 SUBAGENT_FRAME 失败（非关键路径）: %s", exc)
                # 重置版本号，强制下帧重建并重试
                self._last_rendered_version = 0

    def _clear_frame_lines(self) -> None:
        """清除 subagent 面板（通过 bottom_bar 清除下屏面板数据）。

        后续 force_redraw() 会自动在下屏移除面板行。
        """
        if self._last_lines <= 0:
            return

        self._last_lines = 0
        try:
            _chat_ui = get_active_chat_ui()
            if _chat_ui is not None:
                bb = _chat_ui.bottom_bar
                if hasattr(bb, 'set_subagent_frame'):
                    bb.set_subagent_frame([])
        except Exception as exc:
            _logger.debug("清除 subagent 面板失败（非关键路径，静默跳过）: %s", exc)

    # ── 生命周期 ────────────────────────────────────────

    def start(self):
        if self._started:
            return
        self._started = True
        self._stopped = False

        _chat_ui = get_active_chat_ui()
        if _chat_ui is not None:
            self._adapter = _chat_ui.output_adapter
            # ★ 获取 push_cmd 回调（向命令队列推送 SUBAGENT_FRAME 命令）
            self._push_cmd = _chat_ui.push_cmd
            # ★ 保存 DECSTBM 滚动区域底部行号，供帧定位使用
            try:
                se = _chat_ui.bottom_bar.get_scroll_end()
                self._scroll_end = int(se) if se is not None else 0
            except Exception as exc:
                _logger.debug("start: 获取 scroll_end 失败，使用 0: %s", exc)
                self._scroll_end = 0
            # 首次渲染（推送 SUBAGENT_FRAME 命令到队列）
            self._push_frame_cmd()

        # 注册终端 resize 回调
        register_sigwinch_callback(self._on_resize)

        # ★ 注册面板刷新回调到 chat_ui render 线程（10Hz），
        #   替代独立的 500ms 定时器，使 subagent 面板刷新与 render 线程同步。
        try:
            _chat_ui.set_panel_refresh_callback(self._panel_refresh_callback)
        except Exception as exc:
            _logger.debug(
                "注册 panel_refresh_callback 失败（非关键路径，静默跳过）: %s", exc,
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
        """停止显示。

        清除终端上的并行面板。

        Args:
            final: 是否为最终停止
        """
        if self._finished:
            return
        self._finished = True
        self._stopped = True

        # 获取 chat_ui 引用（供后续注销回调和请求重绘使用）
        _chat_ui = None
        try:
            _chat_ui = get_active_chat_ui()
        except Exception as exc:
            _logger.debug("stop: 获取 chat_ui 失败: %s", exc)

        # ★ 注销面板刷新回调（render 线程不再调用）
        if _chat_ui is not None:
            try:
                _chat_ui.set_panel_refresh_callback(None)
            except Exception as exc:
                _logger.debug("注销 panel_refresh_callback 失败: %s", exc)

        # 注销终端 resize 回调
        unregister_sigwinch_callback(self._on_resize)

        # 清除终端帧（通过 bottom_bar 清内存）
        self._clear_frame_lines()

        # ★ 显式请求底部栏重绘（清除内存后再触发终端重绘，
        #   确保 _subagent_lines 变为空使 force_redraw() 的
        #   layout_unchanged 判定为 False，执行真正的终端清屏）
        if _chat_ui is not None:
            try:
                _chat_ui.request_bottom_redraw()
            except Exception as exc:
                _logger.debug("request_bottom_redraw 失败（非关键路径，静默跳过）: %s", exc)

        if self._adapter is not None:
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




# ── 终端尺寸查询 ─────────────────────────────────────
# 复用 TerminalAdapter 的 ioctl 策略获取真实终端宽度。
# 不能依赖 shutil.get_terminal_size()（Android Termux 上返回
# 陈旧环境变量值），必须通过 /dev/tty ioctl 查询。
def _get_terminal_width() -> int:
    """获取终端宽度（列数），优先通过 /dev/tty ioctl 查询。"""
    import os
    import fcntl
    import termios
    import struct

    try:
        fd = os.open("/dev/tty", os.O_RDONLY)
        try:
            data = fcntl.ioctl(fd, termios.TIOCGWINSZ,
                               struct.pack("HHHH", 0, 0, 0, 0))
            rows, cols, _, _ = struct.unpack("HHHH", data)
            return cols if cols > 0 else 80
        finally:
            os.close(fd)
    except Exception as exc:
        _logger.debug("_get_terminal_width ioctl 失败，回退 shutil: %s", exc)
    # 回退
    try:
        import shutil
        return shutil.get_terminal_size().columns
    except Exception as exc:
        _logger.debug("_get_terminal_width shutil 回退失败，使用 80: %s", exc)
        return 80
