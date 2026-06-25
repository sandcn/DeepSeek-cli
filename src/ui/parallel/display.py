"""
并行 Agent 显示 — Claude Code 风格（ChatUI 驱动版）

职责：
  - ParallelDisplay：生命周期控制 + 状态代理 + 刷新调度

渲染路径：ParallelDisplay → push_cmd(CmdSubagentSlotUpdate) → 命令队列
  → TuiStore reducer → TuiState.subagent_slots → strategy.py 内联渲染
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...chat_ui.infrastructure.protocol import PanelContext

from ..output_target import IOutputTarget, TerminalTarget
from ..events.event_bus import DisplayEventBus
from ..events.event_types import LiveOutputEvent
from ._config import DisplayConfig
from ..base_display import BaseDisplay
from ..state.agent_state import AgentStateStore

# 跨层引用说明：CmdSubagentSlotUpdate 是纯数据 dataclass，
# 属于数据契约层。在 chat_ui/commands/types.py 中定义（与所有 Cmd* 类型同文件），
# 保留此 import 作为数据契约引用，避免为两个类型创建独立的共享模块。
from ...chat_ui.commands.types import CmdSubagentSlotUpdate

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值
_DEFAULT_HISTORY = 3
_logger = logging.getLogger(__name__)


class ParallelDisplay(BaseDisplay):
    """并行 Agent 实时显示管理器 — 命令队列渲染版（代理层）

    职责：
    1. 生命周期控制（start/stop）
    2. 状态更新代理（代理到 AgentStateStore）
    3. 面板刷新调度（通过 RenderCommand.SUBAGENT_FRAME 命令队列渲染）
    4. 特殊输出（capture_and_print/print_output）

    状态更新通过 CmdSubagentSlotUpdate 推送到 chat_ui 命令队列，
    由 TuiStore reducer 合并到 TuiState.subagent_slots，
    最终由 strategy.py 内联渲染。
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

        # OutputAdapter（由 start() 中从 ChatUIConsumer 获取）
        self._adapter = None

        # ★ push_cmd 回调（由 start() 从 ChatUIConsumer 获取，线程安全）
        self._push_cmd: Any = None

        # stdout 捕获锁
        self._capture_lock = asyncio.Lock()

        # PanelContext 注入（替代 get_active_chat_ui() 调用）
        self._panel_ctx: "PanelContext | None" = None

    def set_panel_context(self, ctx) -> None:
        """注入 PanelContext（替代 get_active_chat_ui() 调用）。"""
        self._panel_ctx = ctx

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
        """空操作 — 帧刷新的占位方法（保留供外部调用方兼容）。"""

    def _push_slot_update(self, label: str) -> None:
        """将 AgentStateStore 中指定 label 的槽位数据同步到 TuiState。

        从 AgentStateStore 读取最新 slot 快照，转换为可序列化 dict，
        通过 push_cmd 推送 CmdSubagentSlotUpdate 到 chat_ui 命令队列，
        由 TuiStore._reduce_subagent_slot_update 合并到 TuiState.subagent_slots。

        已包含字段：label, description, agent_type, status, start_time, end_time,
            total_calls, input_tokens, output_tokens, live_input_tokens,
            live_output_tokens, last_speed, model_phase, model_info,
            result_text, result_error, tool_history（List[dict]，
            每项含 tool_name/detail/start_time/end_time/phase）。
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
            "tool_history": [
                {
                    "tool_name": r.tool_name,
                    "detail": r.detail,
                    "start_time": r.start_time,
                    "end_time": r.end_time,
                    "phase": r.phase,
                }
                for r in slot.tool_history
            ],
        }
        self._push_cmd(CmdSubagentSlotUpdate(label=label, slot=slot_dict))

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
        # ★ 获取 push_cmd 回调
        self._push_cmd = _chat_ui.push_cmd

    def refresh(self, force: bool = False):
        """公开刷新入口（空操作 — 帧刷新不再通过此方法触发）。"""

    # ── 停止 ────────────────────────────────────────────

    def stop(self, final: bool = False) -> None:
        """停止显示（实现 DisplayPort 接口）。"""
        if self._finished:
            return
        self._finished = True
        self._stopped = True
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

        return await _run()

    def clear_frame_and_run(self, func) -> Any:
        """清除显示帧然后执行 func（func 直接写 stdout）。"""
        return func()

    def print_output(self, text: str):
        """输出文本到终端。"""
        if not text:
            return
        self._terminal.write_line(text)
