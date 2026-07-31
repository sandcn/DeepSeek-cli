"""SubAgent 面板控制器 — 消费 EventBus 事件，渲染 subagent 状态面板。

架构（与旧 ParallelDisplay 同等的终端渲染效果）：
  事件源（EventBusDisplayProxy / parallel_executor）
    → EventBus.publish(AgentAddedEvent, AgentStatusChanged, ModelPhaseEvent, ...)
    → SubAgentPanelController（本模块）
    → 帧渲染（含摘要行/分隔线/树形连接/工具历史/spinner）
    → RenderCommand.SUBAGENT_FRAME 推送
    → TuiEngine._do_subagent_frame()
    → BottomBar.set_subagent_frame() → 终端显示

渲染效果：
  ● 3 agents [████████░░░░] · 12.5k out · 1.2k/s · 15.3s · 2/3 done
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   ├─ ⠋ [EXE] 分析代码结构  12.5k out  1.2k/s  15.3s
   │  …thinking  3.2s
   │  ● read_file /path/to/file.py  0.3s
   │  ✔ grep pattern src/  0.1s
   └─ ✔ [EXE] 生成测试  8.2k out  10.1s
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Tuple

from ._const import RenderCommand

_logger = logging.getLogger(__name__)

# ── 256 色 ANSI 常量 ───────────────────────────────────
_C_RUNNING       = "\033[38;5;214m"   # 琥珀色 — 运行中
_C_DONE          = "\033[38;5;40m"    # 亮绿 — 完成
_C_FAIL          = "\033[38;5;196m"   # 亮红 — 失败
_C_ANSWERING     = "\033[38;5;75m"    # 浅蓝 — 回答中
_C_PARSING       = "\033[38;5;178m"   # 金色 — 解析
_C_BATCH         = "\033[38;5;140m"   # 淡紫 — 批量
_C_DIMMER        = "\033[38;5;240m"   # 暗灰 — 辅助
_C_DIMMEST       = "\033[38;5;238m"   # 深灰 — 分隔线
_C_SUMMARY_DIM   = "\033[38;5;245m"   # 中灰 — 摘要次要
_C_BRANCH        = "\033[38;5;239m"   # 灰 — 树形线
_C_RESET         = "\033[0m"

# ── Agent 类型 → 颜色（从 _tool_icons 导入，此处保留本地副本用于渲染线程） ──
# ── 工具类别 → 颜色 ────────────────────────────────────
_TOOL_CATEGORY_COLORS: Dict[str, str] = {
    "shell":      "\033[38;5;41m",
    "file_read":  "\033[38;5;81m",
    "file_write": "\033[38;5;213m",
    "search":     "\033[38;5;221m",
    "agent":      "\033[38;5;75m",
    "interact":   "\033[38;5;51m",
    "delete":     "\033[38;5;203m",
}

_TOOL_CATEGORY_MAP: Dict[str, str] = {
    "bash": "shell", "execute_command": "shell",
    "read_file": "file_read",
    "write_file": "file_write", "update_file": "file_write",
    "str_replace_editor": "file_write", "file_editor": "file_write",
    "grep": "search", "find": "search", "glob": "search",
    "web_search": "search", "web_fetch": "search",
    "dispatch_agent": "agent",
    "user_select": "interact",
    "rm": "delete",
}

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_INDENT = "  "


def _get_tool_color(tool_name: str) -> str:
    cat = _TOOL_CATEGORY_MAP.get(tool_name, "")
    return _TOOL_CATEGORY_COLORS.get(cat, "\033[38;5;245m")


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}m{s:.0f}s"


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _format_speed(s: float) -> str:
    if s <= 0:
        return "-"
    if s >= 1_000_000:
        return f"{s / 1_000_000:.1f}M/s"
    elif s >= 1_000:
        return f"{s / 1_000:.0f}k/s"
    elif s >= 100:
        return f"{s:.0f}/s"
    elif s >= 1:
        return f"{s:.1f}/s"
    return f"{s:.2f}/s"


# ═══════════════════════════════════════════════════════════
# 状态槽位
# ═══════════════════════════════════════════════════════════

class _ToolRecord:
    __slots__ = ('tool_name', 'detail', 'start_time', 'end_time', 'phase')

    def __init__(self, tool_name: str, detail: str = ""):
        self.tool_name = tool_name
        self.detail = detail
        self.start_time: float = time.time()
        self.end_time: float = 0.0
        self.phase: str = "parsing"  # parsing / running / done / fail


class _AgentSlot:
    __slots__ = (
        'label', 'description', 'status', 'agent_type',
        'start_time', 'end_time',
        'model_phase', 'model_info', 'model_phase_start',
        'parse_info',
        'input_tokens', 'output_tokens',
        'live_input_tokens', 'live_output_tokens',
        'last_speed',
        'tool_history',
        'result_text', 'result_error',
    )

    def __init__(self, label: str, description: str, status: str = "running",
                 agent_type: str = "execute"):
        self.label = label
        self.description = description
        self.status = status
        self.agent_type = agent_type
        self.start_time: float = time.time()
        self.end_time: float = 0.0
        self.model_phase: str = ""
        self.model_info: str = ""
        self.model_phase_start: float = 0.0
        self.parse_info: str = ""
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.live_input_tokens: int = 0
        self.live_output_tokens: int = 0
        self.last_speed: float = 0.0
        self.tool_history: List[_ToolRecord] = []
        self.result_text: str = ""
        self.result_error: str = ""


# ═══════════════════════════════════════════════════════════
# SubAgentPanelController
# ═══════════════════════════════════════════════════════════

class SubAgentPanelController:
    _instance: "SubAgentPanelController | None" = None
    _class_lock = threading.Lock()
    # 帧渲染节流：100ms 间隔（10Hz）
    _EMIT_INTERVAL: float = 0.1

    def __init__(self, max_history: int = 3,
                 push_cmd: Callable[[tuple], None] | None = None):
        self._agents: Dict[str, _AgentSlot] = {}
        self._order: List[str] = []
        # RLock: 允许 _on_tool_parsing 等事件处理器在持有锁时调用 _push_frame(_render_frame()) 而不死锁；_render_frame() 内部也获取此锁
        self._state_lock = threading.RLock()
        self._frame: int = 0
        self._last_emit_time: float = 0.0
        self._active: bool = False
        self._cb_registered: bool = False
        self._chat_ui: Any = None
        self._push_cmd_cb: Callable[[tuple], None] | None = push_cmd
        self.max_history: int = max_history

    @classmethod
    def get_default(cls) -> "SubAgentPanelController":
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 生命周期 ────────────────────────────────────────

    def ensure_active(self) -> None:
        if self._active:
            return
        from .events import DisplayEventBus
        from .events.event_types import (
            AgentAddedEvent, AgentStatusChanged, ModelPhaseEvent,
            ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
            ParseInfoEvent, ParseInfoDoneEvent,
            UsageUpdatedEvent, MetricsUpdateEvent,
        )
        bus = DisplayEventBus.get_default()
        bus.subscribe(self._on_agent_added, event_type=AgentAddedEvent)
        bus.subscribe(self._on_agent_status_changed, event_type=AgentStatusChanged)
        bus.subscribe(self._on_model_phase, event_type=ModelPhaseEvent)
        bus.subscribe(self._on_tool_parsing, event_type=ToolParsingEvent)
        bus.subscribe(self._on_tool_started, event_type=ToolStartedEvent)
        bus.subscribe(self._on_tool_done, event_type=ToolDoneEvent)
        bus.subscribe(self._on_parse_info, event_type=ParseInfoEvent)
        bus.subscribe(self._on_parse_info_done, event_type=ParseInfoDoneEvent)
        bus.subscribe(self._on_usage_updated, event_type=UsageUpdatedEvent)
        bus.subscribe(self._on_metrics, event_type=MetricsUpdateEvent)
        self._register_panel_refresh()
        self._active = True

    def stop(self, clear_panel: bool = True) -> None:
        if not self._active:
            return
        from .events import DisplayEventBus
        from .events.event_types import (
            AgentAddedEvent, AgentStatusChanged, ModelPhaseEvent,
            ToolParsingEvent, ToolStartedEvent, ToolDoneEvent,
            ParseInfoEvent, ParseInfoDoneEvent,
            UsageUpdatedEvent, MetricsUpdateEvent,
        )
        bus = DisplayEventBus.get_default()
        for ev_type, handler in [
            (AgentAddedEvent, self._on_agent_added),
            (AgentStatusChanged, self._on_agent_status_changed),
            (ModelPhaseEvent, self._on_model_phase),
            (ToolParsingEvent, self._on_tool_parsing),
            (ToolStartedEvent, self._on_tool_started),
            (ToolDoneEvent, self._on_tool_done),
            (ParseInfoEvent, self._on_parse_info),
            (ParseInfoDoneEvent, self._on_parse_info_done),
            (UsageUpdatedEvent, self._on_usage_updated),
            (MetricsUpdateEvent, self._on_metrics),
        ]:
            try:
                bus.unsubscribe(handler, event_type=ev_type)
            except Exception:
                pass
        if clear_panel:
            self._push_frame([])
            # 强制立即重绘底部栏，确保面板立即消失
            if self._chat_ui is not None:
                try:
                    self._chat_ui._engine.request_bottom_redraw()
                except Exception:
                    pass
        self._unregister_panel_refresh()
        with self._state_lock:
            self._agents.clear()
            self._order.clear()
        self._active = False

    # ── 事件处理器 ──────────────────────────────────────

    def _emit_frame(self) -> None:
        """渲染并推送当前帧（受 _EMIT_INTERVAL 节流）。

        锁顺序保证（防死锁）：
          ┌─ _render_frame() 内部获取/释放 _state_lock（RLock 可重入）
          └─ _push_frame()  在此函数外层调用，_state_lock 已释放

        调用此函数时调用方不应持有 _state_lock。
        _render_frame 内部获取/释放锁，_push_frame 在锁外调用。
        违反此顺序可能导致 render 线程等待 _state_lock 时形成 ABBA 死锁。
        """
        now = time.time()
        if now - self._last_emit_time < self._EMIT_INTERVAL:
            return  # 节流，跳过本次渲染
        self._last_emit_time = now
        lines = self._render_frame()
        self._push_frame(lines)

    def _on_agent_added(self, event) -> None:
        with self._state_lock:
            if event.label not in self._agents:
                slot = _AgentSlot(
                    label=event.label,
                    description=event.description,
                    status=event.status,
                    agent_type=getattr(event, 'agent_type', 'execute'),
                )
                self._agents[event.label] = slot
                self._order.append(event.label)
        self._emit_frame()

    def _on_agent_status_changed(self, event) -> None:
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            slot.status = event.status
            if event.status in ("done", "fail"):
                slot.end_time = time.time()
                for rec in slot.tool_history:
                    if rec.phase in ("running", "parsing"):
                        rec.phase = "done" if event.status == "done" else "fail"
                        rec.end_time = time.time()
        self._emit_frame()

    def _on_model_phase(self, event) -> None:
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            if event.phase != slot.model_phase:
                slot.model_phase_start = time.time()
            slot.model_phase = event.phase
            slot.model_info = event.info
        self._emit_frame()

    def _on_tool_parsing(self, event) -> None:
        """ToolParsingEvent — 流式解析工具参数时创建/更新 parsing 记录。"""
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            # ★ 更新 model phase 为 parsing，使面板显示 "parsing" 阶段指示
            slot.model_phase = "parsing"
            slot.model_phase_start = time.time()
            # 如果已有同名 parsing 记录，更新 detail（累积参数）
            for rec in reversed(slot.tool_history):
                if rec.tool_name == event.tool_name and rec.phase == "parsing":
                    rec.detail = event.arguments
                    break
            else:
                rec = _ToolRecord(tool_name=event.tool_name)
                rec.detail = event.arguments
                slot.tool_history.append(rec)
        # ★ 强制立即渲染帧（绕过 10Hz 节流），确保 parsing 状态立即可见
        #    锁顺序保证（防死锁）：
        #      _render_frame() 内部获取/释放 _state_lock（RLock 可重入）
        #      _push_frame()   在 _render_frame 返回后调用，锁已释放
        #    注意：_push_frame 绝不可在 with self._state_lock 块内调用，
        #    否则 render 线程在同一锁上阻塞时形成 ABBA 死锁。
        self._push_frame(self._render_frame())

    def _on_parse_info(self, event) -> None:
        """ParseInfoEvent — ToolParseTracker 定时推送的解析摘要（rf,rf 51t 0.74s）。"""
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            tokens_str = f"{event.tokens}t" if isinstance(event.tokens, (int, float)) else str(event.tokens)
            slot.parse_info = f"{event.tool_names} {tokens_str} {event.elapsed:.2f}s"
        self._emit_frame()

    def _on_parse_info_done(self, event) -> None:
        """ParseInfoDoneEvent — 工具解析完成，清除解析摘要和 phase。"""
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            slot.parse_info = ""
            if slot.model_phase == "parsing":
                slot.model_phase = ""
        self._emit_frame()

    def _on_tool_started(self, event) -> None:
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            # 将已有 parsing 记录转换为 running，避免重复创建
            for rec in reversed(slot.tool_history):
                if rec.tool_name == event.tool_name and rec.phase == "parsing":
                    rec.phase = "running"
                    rec.detail = event.detail
                    break
            else:
                rec = _ToolRecord(tool_name=event.tool_name, detail=event.detail)
                rec.phase = "running"
                slot.tool_history.append(rec)
        self._emit_frame()

    def _on_tool_done(self, event) -> None:
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            # 找到最后一个匹配的 running 工具并标记完成
            for rec in reversed(slot.tool_history):
                if rec.tool_name == event.tool_name and rec.phase == "running":
                    rec.phase = "done" if event.success else "fail"
                    rec.end_time = time.time()
                    break
        self._emit_frame()

    def _on_usage_updated(self, event) -> None:
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            usage = event.usage
            if not isinstance(usage, dict):
                return
            replace = getattr(event, 'replace', False)
            if replace:
                slot.input_tokens = usage.get("input", 0)
                slot.output_tokens = usage.get("output", 0)
                slot.live_input_tokens = usage.get("live_input", 0)
                slot.live_output_tokens = usage.get("live_output", 0)
            else:
                slot.input_tokens += usage.get("input", 0)
                slot.output_tokens += usage.get("output", 0)
                slot.live_input_tokens += usage.get("live_input", 0)
                slot.live_output_tokens += usage.get("live_output", 0)
            slot.last_speed = float(usage.get("speed", 0))
        self._emit_frame()

    def _on_metrics(self, event) -> None:
        """MetricsUpdateEvent → 增量更新实时 token 计数和速度。"""
        with self._state_lock:
            slot = self._agents.get(event.label)
            if slot is None:
                return
            if event.live_input_tokens:
                slot.live_input_tokens += event.live_input_tokens
            if event.live_output_tokens:
                slot.live_output_tokens += event.live_output_tokens
            if event.output_tokens:
                slot.output_tokens += event.output_tokens
            if event.speed > 0:
                slot.last_speed = event.speed
        self._emit_frame()

    # ── 面板刷新回调 ────────────────────────────────────

    def _register_panel_refresh(self) -> None:
        if self._push_cmd_cb is not None:
            # 已注入 push_cmd，无需通过 get_active_chat_ui 获取 ChatUIConsumer
            # panel_refresh 回调由 engine 的 panel_refresh_cb 驱动
            return
        from ._consumer import get_active_chat_ui
        chat_ui = get_active_chat_ui()
        if chat_ui is not None:
            chat_ui.set_panel_refresh_callback(self._panel_refresh)
            self._cb_registered = True
            self._chat_ui = chat_ui

    def _unregister_panel_refresh(self) -> None:
        self._cb_registered = False
        if self._chat_ui is not None:
            try:
                self._chat_ui.set_panel_refresh_callback(None)
            except Exception:
                pass
        self._chat_ui = None

    def _panel_refresh(self) -> None:
        if not self._cb_registered:
            self._register_panel_refresh()
        try:
            lines = self._render_frame()
            self._push_frame(lines)
        except Exception:
            _logger.debug("_panel_refresh 异常", exc_info=True)
        self._frame += 1

    # ── 帧渲染（与旧 FrameRenderer 等效的输出格式） ────

    def _render_frame(self) -> List[str]:
        with self._state_lock:
            if not self._agents:
                return []
            now = time.time()
            lines: List[str] = []

            # ── 摘要行 ──
            total = len(self._order)
            done_count = 0
            total_output = 0
            earliest_start: float | None = None
            latest_speed = 0.0
            has_running = False

            for label in self._order:
                slot = self._agents.get(label)
                if slot is None:
                    continue
                disp_out = slot.output_tokens + slot.live_output_tokens
                total_output += disp_out
                if slot.status == "running":
                    has_running = True
                    if slot.last_speed > 0:
                        latest_speed += slot.last_speed
                if slot.status in ("done", "fail"):
                    done_count += 1
                if earliest_start is None or slot.start_time < earliest_start:
                    earliest_start = slot.start_time

            elapsed = (now - earliest_start) if earliest_start else 0
            elapsed_str = _format_duration(elapsed)
            output_str = _format_tokens(total_output)
            speed_str = _format_speed(latest_speed) if has_running else "-"

            sep = f" {_C_DIMMER}\u00b7{_C_RESET} "
            # 简易进度条
            bar_width = min(12, total * 4)
            if done_count < total:
                done_blocks = int(bar_width * done_count / total) if total else 0
                bar = (
                    _C_RUNNING + "\u2588" * done_blocks
                    + _C_DIMMEST + "\u2591" * (bar_width - done_blocks)
                    + _C_RESET
                )
                icon = f"{_C_RUNNING}\u25cf{_C_RESET}"
                summary = (
                    f"{icon} {_C_SUMMARY_DIM}{total} agents{_C_RESET}"
                    f" {bar}"
                    f"{sep}{_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                    f"{sep}{_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                    f"{sep}{_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                    f"{sep}{_C_RUNNING}{done_count}/{total} done{_C_RESET}"
                )
            else:
                bar = _C_DONE + "\u2588" * bar_width + _C_RESET
                icon = f"{_C_DONE}\u2714{_C_RESET}"
                summary = (
                    f"{icon} {_C_DONE}{total} agents{_C_RESET}"
                    f" {bar}"
                    f"{sep}{_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                    f"{sep}{_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                    f"{sep}{_C_DONE}{done_count}/{total} done{_C_RESET}"
                )
            lines.append(summary)

            # ── 分隔线 ──
            lines.append(f"{_C_DIMMEST} \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501{_C_RESET}")

            # ── 各 Agent ──
            prev_has_sublines = False
            for idx, label in enumerate(self._order):
                slot = self._agents.get(label)
                if slot is None:
                    continue
                is_last = (idx == len(self._order) - 1)

                # Agent 间空白延续行
                if idx > 0 and prev_has_sublines:
                    lines.append(f"{_C_BRANCH} \u2502 {_C_RESET}")

                agent_lines = self._build_agent_lines(slot, now, is_last)
                lines.extend(agent_lines)
                prev_has_sublines = len(agent_lines) > 1

            # 清理尾部空行
            while lines and lines[-1] == "":
                lines.pop()
            return lines

    def _build_agent_lines(self, slot: _AgentSlot, now: float, is_last: bool) -> List[str]:
        lines: List[str] = []
        branch = " \u2514\u2500" if is_last else " \u251c\u2500"
        cont   = "   " if is_last else " \u2502 "

        elapsed = (slot.end_time or now) - slot.start_time
        elapsed_str = _format_duration(elapsed)
        disp_out = slot.output_tokens + slot.live_output_tokens
        output_str = _format_tokens(disp_out)
        speed_str = _format_speed(slot.last_speed) if slot.status == "running" else ""

        # ── 类型标签 ──
        from ._tool_icons import AGENT_TYPE_ABBREV, AGENT_TYPE_COLORS
        agent_type_color = AGENT_TYPE_COLORS.get(slot.agent_type, _C_DIMMER)
        abbr = AGENT_TYPE_ABBREV.get(slot.agent_type, "??")
        type_tag = f"{agent_type_color}[{abbr}]{_C_RESET}"

        # ── 状态图标 + 标题行 ──
        if slot.status == "done":
            icon = f"{_C_DONE}\u2714{_C_RESET}"
            suffix = f"  {_C_DIMMER}{output_str}{_C_RESET}  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            title = f"{_C_BRANCH}{branch}{_C_RESET} {icon} {type_tag} {slot.description}{suffix}"
        elif slot.status == "fail":
            icon = f"{_C_FAIL}\u2716{_C_RESET}"
            suffix = f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            title = f"{_C_BRANCH}{branch}{_C_RESET} {icon} {type_tag} {slot.description}{suffix}"
        else:
            spinner = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
            dot = f"{_C_RUNNING}{spinner}{_C_RESET}"
            suffix = (
                f"  {_C_DIMMER}{output_str}{_C_RESET}"
                f"  {_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            )
            title = f"{_C_BRANCH}{branch}{_C_RESET} {dot} {type_tag} {slot.description}{suffix}"
        lines.append(title)

        # ── 阶段指示 ──
        if slot.status == "running" and slot.model_phase:
            phase_elapsed = now - slot.model_phase_start if slot.model_phase_start else 0
            phase_time = f"{phase_elapsed:.1f}s"
            if slot.model_phase == "thinking":
                lines.append(
                    f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}\u2026thinking  {phase_time}")
            elif slot.model_phase == "answering":
                lines.append(
                    f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}{_C_ANSWERING}\u2026answering{_C_DIMMER}  {phase_time}{_C_RESET}")
            elif slot.model_phase == "parsing":
                extra = slot.parse_info or slot.model_info
                lines.append(
                    f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}{_C_PARSING}\u2026parsing{_C_DIMMER}  {extra}{_C_RESET}")
            elif slot.model_phase == "batch":
                lines.append(
                    f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}{_C_BATCH}\u2026batch{_C_DIMMER}  {slot.model_info}  {phase_time}{_C_RESET}")

        # ── 工具历史（仅 running 时展开；done/fail 折叠为单行） ──
        if slot.status not in ("done", "fail"):
            history = slot.tool_history[-self.max_history:]
            for rec in reversed(history):
                lines.append(self._format_tool_record(rec, now, cont))

        return lines

    def _format_tool_record(self, rec: _ToolRecord, now: float, cont: str) -> str:
        elapsed = (rec.end_time or now) - rec.start_time if rec.start_time else 0
        time_str = f"{elapsed:.1f}s"
        detail = rec.detail.replace('\r', '\\r').replace('\n', '\\n') if rec.detail else ""

        from ._tool_icons import TOOL_ICONS
        from src.tools.registry import get_tool_display_name
        tool_icon = TOOL_ICONS.get(rec.tool_name, "")
        display_name = get_tool_display_name(rec.tool_name)
        tool_color = _get_tool_color(rec.tool_name)
        tool_abbr = f"{tool_icon} {tool_color}{display_name}{_C_RESET}" if tool_icon else f"{tool_color}{display_name}{_C_RESET}"
        detail_disp = f" {_C_DIMMER}{detail}{_C_RESET}" if detail else ""
        prefix = f"{_C_BRANCH}{cont}{_C_RESET}{_INDENT}"

        if rec.phase == "parsing":
            line = f"{prefix}{_C_PARSING}\u25cc{_C_RESET} {tool_abbr}{detail_disp}"
        elif rec.phase == "running":
            pulse_color = "\033[38;5;214m"
            line = f"{prefix}{pulse_color}\u25cf{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
        elif rec.phase == "done":
            line = f"{prefix}{_C_DONE}\u2714{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
        else:  # fail
            line = f"{prefix}{_C_FAIL}\u2716{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
        return line

    # ── 帧推送 ──────────────────────────────────────────

    def _push_frame(self, lines: List[str]) -> None:
        # 优先使用注入的 push_cmd 回调，避免 get_active_chat_ui() 循环依赖
        if self._push_cmd_cb is not None:
            try:
                self._push_cmd_cb((RenderCommand.SUBAGENT_FRAME, lines))
                return
            except Exception:
                pass
        # 降级：通过 get_active_chat_ui() 获取
        chat_ui = self._chat_ui
        if chat_ui is None:
            from ._consumer import get_active_chat_ui
            chat_ui = get_active_chat_ui()
            self._chat_ui = chat_ui
        if chat_ui is None:
            return
        try:
            chat_ui.push_cmd((RenderCommand.SUBAGENT_FRAME, lines))
        except Exception:
            pass
