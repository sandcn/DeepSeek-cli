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
from typing import Any, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from ...chat_ui.infrastructure.protocol import PanelContext

from ..output_target import IOutputTarget, TerminalTarget
from ..events.event_bus import DisplayEventBus
from ..events.event_types import LiveOutputEvent
from ._config import DisplayConfig
from ..base_display import BaseDisplay

# CmdSubagentSlotUpdate 定义已迁移至 src.shared_events.types（P3-9）
from src.shared_events.types import CmdSubagentSlotUpdate

# ── 常量 ────────────────────────────────────────────────

_EVENTBUS_THROTTLE = 0.3   # 300ms — EventBus 发布频率阈值
_DEFAULT_HISTORY = 3
_logger = logging.getLogger(__name__)


class ParallelDisplay(BaseDisplay):
    """并行 Agent 实时显示管理器 — 命令队列渲染版

    职责：
    1. 生命周期控制（start/stop）
    2. 状态管理（本地 dict 存储，通过 _push_slot_update 同步到 TuiState）
    3. 面板刷新调度（通过 CmdSubagentSlotUpdate 命令队列渲染）
    4. 特殊输出（capture_and_print/print_output）

    状态更新通过 CmdSubagentSlotUpdate 推送到 chat_ui 命令队列，
    由 TuiStore reducer 合并到 TuiState.subagent_slots，
    最终由 strategy.py 内联渲染。
    """

    def __init__(self, max_history: int = _DEFAULT_HISTORY,
                 output_target: IOutputTarget | None = None):
        super().__init__(output_target=output_target)
        self._slots: Dict[str, Dict[str, Any]] = {}
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
        self._slots[label] = {
            "label": label, "description": description, "agent_type": agent_type,
            "status": status, "start_time": time.time(), "end_time": 0.0,
            "tool_history": [], "total_calls": 0,
            "input_tokens": 0, "output_tokens": 0,
            "live_input_tokens": 0, "live_output_tokens": 0,
            "last_speed": 0.0, "model_phase": "", "model_info": "",
            "result_text": "", "result_error": "",
        }
        self._push_slot_update(label)
        self._schedule_refresh()

    def remove_agent(self, label: str) -> None:
        self._slots.pop(label, None)

    # ── 状态更新 ─────────────────────────────────────

    def update_agent_status(self, label: str, status: str):
        slot = self._slots.get(label)
        if not slot:
            return
        slot["status"] = status
        if status in ("done", "fail"):
            slot["end_time"] = time.time()
            for rec in slot["tool_history"]:
                if rec["phase"] in ("running", "parsing"):
                    rec["phase"] = "done" if status == "done" else "fail"
                    rec["end_time"] = time.time()
        self._push_slot_update(label)
        if status in ("done", "fail"):
            self.remove_agent_slot(label)
        self._schedule_refresh()

    def update_status(self, label: str, status: str):
        return self.update_agent_status(label, status)

    def update_model_phase(self, label: str, phase: str, info: str = ""):
        slot = self._slots.get(label)
        if slot:
            if phase != slot["model_phase"]:
                pass  # model_phase_start not tracked in simple dict
            slot["model_phase"] = phase
            slot["model_info"] = info
            self._push_slot_update(label)
            self._schedule_refresh()

    def tool_parsing(self, label: str, tool_name: str, arguments: str = ""):
        slot = self._slots.get(label)
        if not slot:
            return
        slot["model_phase"] = "parsing"
        truncated = arguments[:120] + "…" if len(arguments) > 120 else arguments
        slot["model_info"] = f"{tool_name} {truncated}" if truncated else tool_name
        if slot["tool_history"]:
            last = slot["tool_history"][-1]
            if last["tool_name"] == tool_name and last["phase"] == "parsing":
                last["detail"] = arguments
                self._push_slot_update(label)
                self._schedule_refresh()
                return
        slot["tool_history"].append({
            "tool_name": tool_name, "detail": arguments,
            "start_time": time.time(), "end_time": 0.0, "phase": "parsing",
        })
        slot["total_calls"] += 1
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_batch_start(self, label: str, tool_names: list):
        slot = self._slots.get(label)
        if not slot:
            return
        names_str = ", ".join(tool_names)
        slot["model_phase"] = "batch"
        slot["model_info"] = f"{len(tool_names)}x parallel: {names_str}"
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_start(self, label: str, tool_name: str, detail: str = "",
                   metadata: dict | None = None):
        slot = self._slots.get(label)
        if not slot:
            return
        for rec in reversed(slot["tool_history"]):
            if rec["phase"] == "parsing" and rec["tool_name"] == tool_name:
                rec["detail"] = detail
                rec["phase"] = "running"
                self._push_slot_update(label)
                self._schedule_refresh()
                return
        slot["tool_history"].append({
            "tool_name": tool_name, "detail": detail,
            "start_time": time.time(), "end_time": 0.0, "phase": "running",
        })
        self._push_slot_update(label)
        self._schedule_refresh()

    def tool_done(self, label: str, tool_name: str = "",
                  success: bool = True, metadata: dict | None = None):
        slot = self._slots.get(label)
        if not slot:
            return
        for rec in reversed(slot["tool_history"]):
            if rec["phase"] in ("running", "parsing"):
                if tool_name:
                    if rec["tool_name"] == tool_name:
                        rec["phase"] = "done" if success else "fail"
                        rec["end_time"] = time.time()
                        break
                else:
                    rec["phase"] = "done" if success else "fail"
                    rec["end_time"] = time.time()
                    break
        self._push_slot_update(label)
        self._schedule_refresh()

    def update_parse_info(self, label: str, tool_names: str,
                          tokens: int, elapsed: float):
        slot = self._slots.get(label)
        if slot:
            slot["model_phase"] = "parsing"
            slot["model_info"] = f"{tool_names} {elapsed:.1f}s"
            self._push_slot_update(label)
            self._schedule_refresh()

    def parse_info_done(self, label: str) -> None:
        pass

    def update_tokens(self, label: str, tokens: int):
        slot = self._slots.get(label)
        if slot:
            slot["output_tokens"] += tokens
            self._push_slot_update(label)

    def update_usage(self, label: str, usage: dict, replace: bool = False):
        slot = self._slots.get(label)
        if not slot:
            return
        if replace:
            if "input" in usage:
                slot["input_tokens"] = usage["input"]
            if "output" in usage:
                slot["output_tokens"] = usage["output"]
            slot["live_input_tokens"] = 0
            slot["live_output_tokens"] = 0
        else:
            slot["input_tokens"] += usage.get("input", 0)
            slot["output_tokens"] += usage.get("output", 0)
        speed = usage.get("speed", 0.0)
        if speed and speed > 0:
            slot["last_speed"] = speed
        self._push_slot_update(label)

    def update_live_output(self, label: str, tokens: int):
        slot = self._slots.get(label)
        if slot:
            slot["live_output_tokens"] += tokens
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
        slot = self._slots.get(label)
        if slot and slot["input_tokens"] == 0:
            slot["live_input_tokens"] = tokens
            self._push_slot_update(label)

    def update_speed(self, label: str, speed: float):
        slot = self._slots.get(label)
        if slot and speed > 0:
            slot["last_speed"] = speed
            self._push_slot_update(label)

    def set_result(self, label: str, result_text: str = "", error: str = ""):
        slot = self._slots.get(label)
        if slot:
            slot["result_text"] = result_text
            slot["result_error"] = error
            if slot["status"] in ("done", "fail"):
                self.remove_agent_slot(label)
            else:
                self._push_slot_update(label)
            self._schedule_refresh()

    # ── 帧渲染（通过命令队列） ────────────────────────

    def _schedule_refresh(self) -> None:
        """空操作 — 帧刷新的占位方法（保留供外部调用方兼容）。"""

    def _push_slot_update(self, label: str) -> None:
        """将本地 _slots 中指定 label 的槽位数据同步到 TuiState。

        从 self._slots 读取 dict，通过 push_cmd 推送 CmdSubagentSlotUpdate
        到 chat_ui 命令队列，由 TuiStore._reduce_subagent_slot_update 合并到
        TuiState.subagent_slots。

        已包含字段：label, description, agent_type, status, start_time, end_time,
            total_calls, input_tokens, output_tokens, live_input_tokens,
            live_output_tokens, last_speed, model_phase, model_info,
            result_text, result_error, tool_history（List[dict]，
            每项含 tool_name/detail/start_time/end_time/phase）。
        """
        if self._push_cmd is None:
            return
        slot = self._slots.get(label)
        if slot is None:
            return
        # slot 已经是 dict，直接传递（浅拷贝以保证 reducer 侧不可变语义）
        self._push_cmd(CmdSubagentSlotUpdate(label=label, slot=dict(slot)))

    def remove_agent_slot(self, label: str) -> None:
        """从 TuiState.subagent_slots 中清除指定 agent 的 slot 条目。

        推送空 slot dict 给 reducer 触发删除（参见 _reduce_subagent_slot_update）。
        幂等：label 不存在时无害。
        """
        if self._push_cmd is not None:
            self._push_cmd(CmdSubagentSlotUpdate(label=label, slot={}))

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
