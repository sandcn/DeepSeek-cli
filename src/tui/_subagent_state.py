"""SubAgent 面板状态建模（Layer 0 约束：仅依赖 _const/_config/标准库）。

方向C 步骤7：从 ``_subagent_panel.SubAgentPanelController`` 上帝类拆出的
状态建模域——``_AgentSlot`` / ``_ToolRecord`` 槽位与 ``StateStore``
（锁保护的全部状态变更操作 + 动画需求判定）。

设计模式: 状态（State）— 状态建模与事件/渲染解耦。

职责边界：
  - 状态槽位：``_ToolRecord`` / ``_AgentSlot``（原样迁移，零逻辑变更）
  - 变更操作：``StateStore`` 的 add/update/start/done 系列方法，
    全部在 ``_state_lock``（RLock 可重入）内执行；
  - 查询辅助：``needs_animation``（供渲染短路判定）、``clear``。

锁纪律（安全约束，防死锁）：
  - ``_push_frame`` 绝不可在 ``_state_lock`` 内调用；
  - 渲染模块以 store 快照为输入，``_render_frame`` 内部获取/释放锁；
  - 事件处理器调用 store 变更方法（内部取锁）后，在锁外触发 ``_emit_frame``。

依赖约束：仅标准库 + ``_const/_config``（不依赖渲染/事件订阅，无父包依赖）。
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List

#: BUG-55（review 方向）：工具历史条数上限——同名工具连续调用且前次 done
#: 事件丢失/乱序时旧 running 记录残留（工具历史泄漏）；无界增长下长会话
#: 内存累积 + 渲染历史无限。超出上限弹出最旧记录（渲染仅显示最近
#: ``max_history`` 条，旧记录无消费方）。
_MAX_TOOL_HISTORY = 50


class _ToolRecord:
    __slots__ = ('tool_name', 'tool_id', 'detail', 'start_time', 'end_time', 'phase')

    def __init__(self, tool_name: str, detail: str = "", tool_id: str = ""):
        self.tool_name = tool_name
        # ★ P1-1（review 方向）：工具调用唯一 ID（tool_call_id）——同名工具
        #   连续调用且事件乱序/交叉时（A start → B start → A done），done_tool
        #   按 tool_name 从尾部匹配会闭合错误的 running 记录。tool_id 缺省
        #   空串（旧调用/旧事件不带 tool_id 时降级按 tool_name 匹配，兼容）。
        self.tool_id = tool_id
        self.detail = detail
        self.start_time: float = time.time()
        self.end_time: float = 0.0
        self.phase: str = "parsing"  # parsing / running / done / fail


class _AgentSlot:
    __slots__ = (
        'label', 'description', 'status', 'agent_type',
        'start_time', 'end_time',
        'appear_time',
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
        # BEAUTY-1：FadeIn 出现时刻（时间基，time.monotonic()）
        self.appear_time: float = time.monotonic()
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


class StateStore:
    """SubAgent 面板状态存储（单一真源）。

    全部状态变更操作经锁保护（``_state_lock``，RLock 可重入——
    事件处理器在持有锁时调用渲染函数不致死锁）。控制器持有本实例，
    并通过 ``_agents`` / ``_order`` / ``_state_lock`` 暴露同一引用
    （兼容既有测试直接访问路径）。
    """

    def __init__(self, max_history: int = 3):
        self._agents: Dict[str, _AgentSlot] = {}
        self._order: List[str] = []
        # RLock: 允许事件处理器在持有锁时调用渲染函数（渲染内部取锁）而不死锁
        self._state_lock = threading.RLock()
        self.max_history: int = max_history

    # ── 变更操作（全部在 _state_lock 内执行） ──────────

    @staticmethod
    def _append_record(slot, rec) -> None:
        """追加工具记录并限制历史条数（BUG-55：防无界增长/残留累积）。"""
        slot.tool_history.append(rec)
        if len(slot.tool_history) > _MAX_TOOL_HISTORY:
            slot.tool_history.pop(0)

    @staticmethod
    def _find_record(slot, tool_name: str, tool_id: str,
                     phases: tuple, fallback: bool = True) -> _ToolRecord | None:
        """按 tool_id 精确匹配工具记录；tool_id 为空时优先匹配无 tool_id
        记录，未命中且 fallback 时按 tool_name 降级匹配。

        ★ P1-1/P2-7（review 方向）：
          - tool_id 非空时**仅精确匹配**（不按 tool_name 降级）——同名工具
            连续调用且事件乱序/交叉（A start → B start，不同 tool_id）必须
            各自建立记录；若按 tool_name 降级会把 A/B 合并成一条，破坏精确
            闭合能力。
          - tool_id 为空时**优先精确匹配 tool_id=="" 的记录**——避免无
            tool_id 事件（旧调用方/旧事件）误合并到带 tool_id 的同名记录
            （如带 tool_id 的 A 与不带 id 的 B 并存时必须各自独立）。
          - fallback=True 时（update_tool_parsing/done_tool）：tool_id="" 精确
            匹配未命中再按 tool_name 降级（纯旧路径/旧数据兼容——全部记录
            均无 tool_id 时按 tool_name 合并；done_tool 兜底闭合场景）。
          - fallback=False 时（start_tool）：无 tool_id 记录未命中则新建，
            **不降级合并到带 tool_id 的记录**（start 是新建强信号，合并会
            破坏两条独立调用的记录隔离）。
        返回匹配记录；无匹配返回 None。
        """
        if tool_id:
            for rec in reversed(slot.tool_history):
                if rec.tool_id == tool_id and rec.phase in phases:
                    return rec
            return None
        for rec in reversed(slot.tool_history):
            if rec.tool_id == "" and rec.tool_name == tool_name \
                    and rec.phase in phases:
                return rec
        if fallback:
            for rec in reversed(slot.tool_history):
                if rec.tool_name == tool_name and rec.phase in phases:
                    return rec
        return None

    def add_agent(self, label: str, description: str, status: str = "running",
                  agent_type: str = "execute") -> None:
        with self._state_lock:
            if label not in self._agents:
                slot = _AgentSlot(
                    label=label,
                    description=description,
                    status=status,
                    agent_type=agent_type,
                )
                self._agents[label] = slot
                self._order.append(label)

    def update_status(self, label: str, status: str) -> None:
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            slot.status = status
            # ★ 修复（review 方向）："error" 同为终态（事件类型明确列出，
            #   渲染层按终态处理）——修复前仅 done/fail 终结：
            #   error 代理 end_time 恒 0（面板时长持续增长）+ running/parsing
            #   工具记录不闭合 → needs_animation 恒 True（面板 10Hz 空转）。
            if status in ("done", "fail", "error"):
                slot.end_time = time.time()
                for rec in slot.tool_history:
                    if rec.phase in ("running", "parsing"):
                        rec.phase = "done" if status == "done" else "fail"
                        rec.end_time = time.time()

    def set_model_phase(self, label: str, phase: str, info: str) -> None:
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            if phase != slot.model_phase:
                slot.model_phase_start = time.time()
            slot.model_phase = phase
            slot.model_info = info

    def update_tool_parsing(self, label: str, tool_name: str,
                            arguments: str, tool_id: str = "") -> None:
        """ToolParsingEvent — 流式解析工具参数时创建/更新 parsing 记录。

        Args:
            tool_id: 工具调用唯一 ID（tool_call_id）；缺省空串时降级按
                tool_name 匹配（旧调用方兼容）。
        """
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            # 更新 model phase 为 parsing，使面板显示 "parsing" 阶段指示
            # ★ BUG-59（review 方向）：仅 phase 变化时重置起始时间——修复前
            #   每次流式 parsing 事件（逐段到达）都 ``model_phase_start =
            #   time.time()``，阶段时间基不断归零（"…parsing 0.0s" 恒显示）。
            if slot.model_phase != "parsing":
                slot.model_phase_start = time.time()
            slot.model_phase = "parsing"
            # 按 tool_id 精确匹配 parsing 记录（tool_id 为空时按 tool_name
            # 匹配——P1-1：同名工具乱序事件不更新错记录）；未命中新建
            # parsing 记录
            rec = self._find_record(slot, tool_name, tool_id, ("parsing",))
            if rec is not None:
                rec.detail = arguments
            else:
                rec = _ToolRecord(tool_name=tool_name, tool_id=tool_id)
                rec.detail = arguments
                self._append_record(slot, rec)  # BUG-55：历史条数上限

    def start_tool(self, label: str, tool_name: str, detail: str,
                   tool_id: str = "") -> None:
        """ToolStartedEvent — 工具开始执行，创建/升级 running 记录。

        Args:
            tool_id: 工具调用唯一 ID（tool_call_id）；缺省空串时降级按
                tool_name 匹配（旧调用方兼容）。
        """
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            # ★ 方向1（parsing 阶段清除修复）：工具转为 running 时清除
            #   model_phase 的 "parsing" 指示（update_tool_parsing 设置的）——
            #   修复前工具运行/完成后面板仍显示 "…parsing"，直到后续
            #   set_model_phase/clear_parse_info 到达。
            if slot.model_phase == "parsing":
                slot.model_phase = ""
            # ★ P1-1/P2-7（review 方向）：按 tool_id 精确匹配 parsing/running
            #   记录（未命中/无 tool_id 时降级按 tool_name 匹配）——重复/乱序
            #   start 事件下**不产生重复 running 记录**（修复前仅转换 parsing
            #   记录，已有同名 running 记录时新建重复记录；且未按 tool_id
            #   区分，A start → B start（同名）→ A 再 start 会新建第三条）。
            #   fallback=False：无 tool_id 的 start 不合并到带 tool_id 的同名
            #   记录（带 id 与不带 id 的调用各自独立成记录）。
            rec = self._find_record(
                slot, tool_name, tool_id, ("parsing", "running"),
                fallback=False,
            )
            if rec is not None:
                rec.phase = "running"
                rec.detail = detail
            else:
                rec = _ToolRecord(tool_name=tool_name, detail=detail,
                                  tool_id=tool_id)
                rec.phase = "running"
                self._append_record(slot, rec)  # BUG-55：历史条数上限

    def done_tool(self, label: str, tool_name: str, success: bool,
                  tool_id: str = "") -> None:
        """ToolDoneEvent — 工具执行完成，闭合匹配的 running 记录。

        Args:
            tool_id: 工具调用唯一 ID（tool_call_id）；缺省空串时按 tool_name
                匹配（旧调用方兼容）。
        """
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            # ★ P1-1（review 方向）：按 tool_id 精确匹配 running 记录——同名
            #   工具连续调用且事件乱序/交叉时（A start → B start → A done），
            #   A 的 done 精确闭合 A 的记录而非错误闭合 B（修复前仅按
            #   tool_name 从尾部匹配 → A 记录永久残留 running → 面板 10Hz
            #   持续空转渲染）。
            rec = self._find_record(slot, tool_name, tool_id, ("running",))
            # ★ 降级兜底（review 方向）：done 带 tool_id 但精确匹配失败（start
            #   记录缺失/未带 tool_id）时，按 tool_name 从尾部闭合最后一个
            #   running——done 是终态强信号，闭合优于残留 running 空转。
            if rec is None and tool_id:
                rec = self._find_record(slot, tool_name, "", ("running",))
            if rec is not None:
                rec.phase = "done" if success else "fail"
                rec.end_time = time.time()

    def set_parse_info(self, label: str, tool_names: str, tokens,
                       elapsed: float) -> None:
        """ParseInfoEvent — ToolParseTracker 定时推送的解析摘要（rf,rf 51t 0.74s）。"""
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            tokens_str = f"{tokens}t" if isinstance(tokens, (int, float)) else str(tokens)
            # ★ P2：elapsed 无类型防御——事件字段可能为 None/非数值时
            #   f"{elapsed:.2f}s" 抛 TypeError（与 update_usage 的防御一致），
            #   解析失败回退 0.00s。
            try:
                elapsed_str = f"{elapsed:.2f}s"
            except (TypeError, ValueError):
                elapsed_str = "0.00s"
            slot.parse_info = f"{tool_names} {tokens_str} {elapsed_str}"

    def clear_parse_info(self, label: str) -> None:
        """ParseInfoDoneEvent — 工具解析完成，清除解析摘要和 phase。"""
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            slot.parse_info = ""
            if slot.model_phase == "parsing":
                slot.model_phase = ""

    def update_usage(self, label: str, usage, replace: bool = False) -> None:
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            if not isinstance(usage, dict):
                return
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
            # ★ BUG（review 方向）：``float(usage.get("speed", 0))`` 在 speed 为
            #   None/非数字时抛 TypeError/ValueError——事件处理器无本地 try，异常被
            #   EventBus.publish 捕获后该事件全部 token/速度更新丢失（面板统计静默
            #   停滞）。加类型防护：解析失败回退 0.0。
            try:
                slot.last_speed = float(usage.get("speed", 0))
            except (TypeError, ValueError):
                slot.last_speed = 0.0

    def update_metrics(self, label: str, live_input_tokens: int,
                       live_output_tokens: int, output_tokens: int,
                       speed: float) -> None:
        """MetricsUpdateEvent → 增量更新实时 token 计数和速度。"""
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            if live_input_tokens:
                slot.live_input_tokens += live_input_tokens
            if live_output_tokens:
                slot.live_output_tokens += live_output_tokens
            if output_tokens:
                slot.output_tokens += output_tokens
            # ★ P2：speed 无类型防御——事件字段可能为 None/非数值时
            #   ``speed > 0`` 抛 TypeError（与 update_usage 的防御一致），
            #   无效速度回退 0.0。
            try:
                speed_f = float(speed)
            except (TypeError, ValueError):
                speed_f = 0.0
            if speed_f > 0:
                slot.last_speed = speed_f

    # ── 查询辅助 ─────────────────────────────────────

    def needs_animation(self) -> bool:
        """是否存在活跃/动画状态（running agent / running tool）需要重绘推进。

        PERF-2：空闲（无事件 + 无动画需求）时 ``_panel_refresh`` 短路跳过
        全量渲染（保持动画时仍按 10Hz 渲染）。
        """
        with self._state_lock:
            for label in self._order:
                slot = self._agents.get(label)
                if slot is None:
                    continue
                if slot.status == "running":
                    return True
                for rec in slot.tool_history:
                    if rec.phase in ("running", "parsing"):
                        return True
        return False

    def clear(self) -> None:
        """清空全部状态（stop 清理路径）。"""
        with self._state_lock:
            self._agents.clear()
            self._order.clear()


__all__ = ["_AgentSlot", "_ToolRecord", "StateStore"]
