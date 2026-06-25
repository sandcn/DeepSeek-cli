"""Agent 状态管理 — 纯状态数据类与线程安全的状态存储。"""

import logging
import threading
import time
from dataclasses import field, replace
from src._compat import dataclass
from typing import Dict, List, Literal, Optional

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolRecord:
    """工具调用记录。"""
    tool_name: str
    detail: str
    start_time: float = 0.0
    end_time: float = 0.0
    phase: Literal["parsing", "running", "done", "fail"] = "parsing"


@dataclass(slots=True)
class AgentSlot:
    """单个 Agent 的状态槽位。"""
    label: str
    description: str
    agent_type: str = "plan_execute"
    status: Literal["running", "done", "fail"] = "running"
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    tool_history: List[ToolRecord] = field(default_factory=list)
    total_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    live_input_tokens: int = 0
    live_output_tokens: int = 0
    last_speed: float = 0.0
    model_phase: str = ""
    model_phase_start: float = 0.0
    model_info: str = ""
    result_text: str = ""
    result_error: str = ""

    def deep_copy(self) -> "AgentSlot":
        """返回深拷贝副本（tool_history 中的 ToolRecord 也做 replace）。"""
        return AgentSlot(
            label=self.label, description=self.description, agent_type=self.agent_type,
            status=self.status, start_time=self.start_time,
            end_time=self.end_time,
            tool_history=[replace(r) for r in self.tool_history],
            total_calls=self.total_calls,
            input_tokens=self.input_tokens, output_tokens=self.output_tokens,
            live_input_tokens=self.live_input_tokens,
            live_output_tokens=self.live_output_tokens,
            last_speed=self.last_speed, model_phase=self.model_phase,
            model_phase_start=self.model_phase_start, model_info=self.model_info,
            result_text=self.result_text, result_error=self.result_error,
        )


class AgentStateStore:
    """@deprecated: Agent 状态存储 — 纯状态管理，无渲染/输出逻辑。

    ⚠ 已由 TuiState.subagent_slots 取代。ParallelDisplay 现在双写到
    AgentStateStore（本地状态）和 TuiState（全局状态），帧渲染也从
    AgentStateStore.snapshot_all() 迁移到 TuiState.subagent_slots。

    保留本类以支持过渡期兼容，计划阶段三完成后物理删除。

    线程安全。提供状态更新和快照方法，供 ParallelDisplay 消费。
    """

    def __init__(self):
        import warnings
        import os
        if not os.environ.get("CHAT_UI_RENDER_LEGACY_FALLBACK", "").strip().lower() in ("1", "true", "yes", "on"):
            warnings.warn(
                "AgentStateStore is deprecated. Use TuiState.subagent_slots instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._slots: Dict[str, AgentSlot] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()
        self._version: int = 0  # 状态版本号，每次变化递增

    # ── 注册 ──

    def add_agent(self, label: str, description: str, status: str = "running",
                  agent_type: str = "plan_execute") -> None:
        with self._lock:
            slot = AgentSlot(label=label, description=description,
                             status=status, agent_type=agent_type)
            self._slots[label] = slot
            self._order.append(label)
            self._version += 1

    def remove_agent(self, label: str) -> None:
        with self._lock:
            self._slots.pop(label, None)
            if label in self._order:
                self._order.remove(label)
            self._version += 1

    # ── 状态更新 ──

    def update_agent_status(self, label: str, status: str) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return
            slot.status = status
            if status in ("done", "fail"):
                slot.end_time = time.time()
                for rec in slot.tool_history:
                    if rec.phase in ("running", "parsing"):
                        rec.phase = "done" if status == "done" else "fail"
                        rec.end_time = time.time()
            self._version += 1

    def update_model_phase(self, label: str, phase: str, info: str = "") -> None:
        with self._lock:
            slot = self._slots.get(label)
            if slot:
                if phase != slot.model_phase:
                    slot.model_phase_start = time.time()
                slot.model_phase = phase
                slot.model_info = info
                self._version += 1

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return
            # 流式参数会分多个 chunk 到达，同一工具的后续 chunk
            # 应更新已有 parsing 记录的 detail，而非追加新记录。
            if slot.tool_history:
                last = slot.tool_history[-1]
                if last.tool_name == tool_name and last.phase == "parsing":
                    last.detail = arguments
                    self._version += 1
                    return
            rec = ToolRecord(
                tool_name=tool_name,
                detail=arguments,
                start_time=time.time(),
                phase="parsing",
            )
            slot.tool_history.append(rec)
            slot.total_calls += 1
            self._version += 1

    def tool_batch_start(self, label: str, tool_names: list) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return
            names_str = ", ".join(tool_names)
            slot.model_phase = "batch"
            slot.model_phase_start = time.time()
            slot.model_info = f"{len(tool_names)}x parallel: {names_str}"
            self._version += 1

    def tool_start(self, label: str, tool_name: str, detail: str = "") -> None:
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return
            for rec in reversed(slot.tool_history):
                if rec.phase == "parsing" and rec.tool_name == tool_name:
                    rec.detail = detail
                    rec.phase = "running"
                    self._version += 1
                    return
            rec = ToolRecord(
                tool_name=tool_name,
                detail=detail,
                start_time=time.time(),
                phase="running",
            )
            slot.tool_history.append(rec)
            self._version += 1

    def tool_done(self, label: str, tool_name: str = "", success: bool = True) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return
            if not tool_name:
                _logger.warning(
                    "tool_done 收到空 tool_name (label=%s, success=%s)，"
                    "将使用最后一个 running/parsing 记录",
                    label, success,
                )
            for rec in reversed(slot.tool_history):
                if rec.phase in ("running", "parsing"):
                    if tool_name:
                        if rec.tool_name == tool_name:
                            rec.phase = "done" if success else "fail"
                            rec.end_time = time.time()
                            self._version += 1
                            break
                    else:
                        # 无 tool_name 时自动匹配最后一个活跃记录
                        rec.phase = "done" if success else "fail"
                        rec.end_time = time.time()
                        self._version += 1
                        break

    def update_usage(self, label: str, usage: dict, replace: bool = False) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return
            if replace:
                if "input" in usage:
                    slot.input_tokens = usage["input"]
                if "output" in usage:
                    slot.output_tokens = usage["output"]
                slot.live_input_tokens = 0
                slot.live_output_tokens = 0
            else:
                slot.input_tokens += usage.get("input", 0)
                slot.output_tokens += usage.get("output", 0)
            speed = usage.get("speed", 0.0)
            if speed and speed > 0:
                slot.last_speed = speed
            self._version += 1

    def update_tokens(self, label: str, tokens: int) -> None:
        self.update_usage(label, {"output": tokens})

    def update_live_output(self, label: str, tokens: int) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if slot:
                slot.live_output_tokens += tokens
                self._version += 1

    def update_live_input(self, label: str, tokens: int) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if slot and slot.input_tokens == 0:
                slot.live_input_tokens = tokens
                self._version += 1

    def update_speed(self, label: str, speed: float) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if slot and speed > 0:
                slot.last_speed = speed
                self._version += 1

    def update_parse_info(self, label: str, tool_names: str, tokens: int, elapsed: float) -> None:
        with self._lock:
            slot = self._slots.get(label)
            if slot:
                slot.model_phase = "parsing"
                slot.model_info = f"{tool_names} {elapsed:.1f}s"
                self._version += 1

    def set_result(self, label: str, result_text: str = "", error: str = "") -> None:
        """存储 SubAgent 的执行结果"""
        with self._lock:
            slot = self._slots.get(label)
            if slot:
                slot.result_text = result_text
                slot.result_error = error
                self._version += 1

    # ── 快照与查询 ──

    def get_slot(self, label: str) -> Optional[AgentSlot]:
        """获取指定 Agent 的当前状态快照（深拷贝 ToolRecord 列表）。"""
        with self._lock:
            slot = self._slots.get(label)
            if not slot:
                return None
            return slot.deep_copy()

    def get_order(self) -> List[str]:
        with self._lock:
            return list(self._order)

    def snapshot_all(self) -> Dict[str, AgentSlot]:
        """获取所有 Agent 的快照（深拷贝）。"""
        with self._lock:
            return {
                k: v.deep_copy()
                for k, v in self._slots.items()
            }

    @property
    def agent_count(self) -> int:
        with self._lock:
            return len(self._order)

    @property
    def has_running_agents(self) -> bool:
        """是否有 running 状态的 Agent（快速检查，不做深拷贝）。"""
        with self._lock:
            return any(s.status == "running" for s in self._slots.values())

    @property
    def version(self) -> int:
        """获取当前状态版本号（每次状态变化递增）。"""
        with self._lock:
            return self._version
