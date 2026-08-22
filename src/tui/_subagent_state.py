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
        'dispatch_label',
        'start_time', 'end_time',
        'appear_time',
        'model_phase', 'model_info', 'model_phase_start',
        'parse_info',
        'input_tokens', 'output_tokens',
        'live_input_tokens', 'live_output_tokens',
        'last_speed',
        'tool_history',
        'result_text', 'result_error',
        'messages', 'prompt',
        'live_reasoning', 'live_content',
        '_prev_phase',
        # ★ 2026-08-19（轨迹 Trace 性能优化）：live 内容换行拆分缓存
        #   （``_slot_live_lines`` 用——(文本, 行列表)，内容变化重新拆分）
        '_live_lines_cache',
    )

    def __init__(self, label: str, description: str, status: str = "running",
                 agent_type: str = "execute", dispatch_label: str = ""):
        self.label = label
        self.description = description
        self.status = status
        self.agent_type = agent_type
        # ★ 2026-08-17（用户需求：agent 内容合并到 subagent）：
        #   所属 subagent 工具的 label（tool_call_id）——主轨迹台账按
        #   此把 subagent 记录合并到对应 subagent 工具调用记录（不
        #   分两条）；空串 = 无关联 dispatch（独立执行/历史恢复槽位）。
        self.dispatch_label = dispatch_label
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
        # ★ 2026-08-16（轨迹 Trace 嵌套）：SubAgent 完整消息列表引用
        #   （``register_subagent`` 注入 SubAgent.messages 同一列表对象——
        #   实时增长，轨迹视图显示 subagent 轨迹与 mainagent 同构）；
        #   未注册（异常/未装配）为空列表。
        self.messages: List[dict] = []
        # SubAgent 初始提词（user 消息；供无 messages 时回退显示）
        self.prompt: str = ""
        # ★ 2026-08-16（subagent 动态部分——跟 mainagent 一样动态显示）：
        #   流式生成中的实际内容累积（ReasoningChunkEvent/ContentChunkEvent
        #   ——SubAgent 模型调用为流式管线，chunk 事件带 label 发布；轨迹
        #   视图据此动态显示正在生成的思考/回答，与 mainagent 开放块同
        #   语义）。新阶段开始（set_model_phase 到 thinking/answering）时
        #   重置对应累积（当前轮内容；旧轮已由 messages 记录接管）。
        self.live_reasoning: str = ""
        self.live_content: str = ""
        # 上一模型阶段（set_model_phase 阶段切换检测用；__slots__ 白名单）
        self._prev_phase: str = ""
        # live 内容换行拆分缓存（_slot_live_lines 用；None=未缓存）
        self._live_lines_cache = None


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
                  agent_type: str = "execute", dispatch_label: str = "") -> None:
        with self._state_lock:
            if label not in self._agents:
                slot = _AgentSlot(
                    label=label,
                    description=description,
                    status=status,
                    agent_type=agent_type,
                    dispatch_label=dispatch_label,
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
            # ★ 2026-08-16（subagent 动态部分）：新阶段开始重置对应流式
            #   累积——thinking 开始清空 live_reasoning、answering 开始
            #   清空 live_content（当前轮内容；旧轮已由 messages 记录
            #   接管，避免跨轮拼接）。仅阶段首次进入时重置（phase 与
            #   当前不同），重复事件（同阶段 chunk）不打断累积。
            if phase == "thinking" and phase != getattr(slot, "_prev_phase", ""):
                slot.live_reasoning = ""
            elif phase == "answering" and phase != getattr(slot, "_prev_phase", ""):
                slot.live_content = ""
            slot._prev_phase = phase

    def append_live(self, label: str, kind: str, text: str) -> None:
        """累积 subagent 流式生成内容（ReasoningChunkEvent/ContentChunkEvent）。

        Args:
            label: subagent 标识（非 subagent label 无槽位，零成本跳过）。
            kind: "reasoning" | "content"。
            text: 内容增量。
        """
        if not text:
            return
        with self._state_lock:
            slot = self._agents.get(label)
            if slot is None:
                return
            if kind == "reasoning":
                slot.live_reasoning += text
            elif kind == "content":
                slot.live_content += text

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
            # ★ BUG（2026-08-16，显示多一行修复）：迟到的 parsing 事件不新建
            #   残留记录——同 tool_id 已存在任意阶段记录（running/done/fail）
            #   说明该工具调用已开始/已闭合，后续再到达的 parsing 是重复/迟到
            #   事件（如事件乱序），新建会残留 ◌ parsing 行使面板同一工具显示
            #   两行。tool_id 全局唯一（tool_call_id），同 tool_id 必属同一
            #   调用，忽略不误伤其他调用；tool_id 为空时保持原新建行为
            #   （执行路径 parsing 总是在 start 前到达，无迟到语义）。
            elif not self._has_tool_record(slot, tool_id):
                rec = _ToolRecord(tool_name=tool_name, tool_id=tool_id)
                rec.detail = arguments
                self._append_record(slot, rec)  # BUG-55：历史条数上限

    @staticmethod
    def _has_tool_record(slot, tool_id: str) -> bool:
        """同 tool_id 是否已存在任意阶段记录（迟到 parsing 事件防御）。"""
        if not tool_id:
            return False
        return any(rec.tool_id == tool_id for rec in slot.tool_history)

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
            #   第一轮 fallback=False：无 tool_id 的 start 不合并到带 tool_id
            #   的记录（带 id 与不带 id 的调用各自独立成记录）。
            rec = self._find_record(
                slot, tool_name, tool_id, ("parsing", "running"),
                fallback=False,
            )
            # ★ BUG（2026-08-16，显示多一行修复）：第二轮降级**仅认领带
            #   tool_id 的 parsing 记录**——SubAgent 工具调用的流式 parsing
            #   事件（api/stream/handlers/tool_calls.py）带 tool_id
            #   （_stream_label），而执行阶段 start 曾不传 tool_id：不降级
            #   认领会把同一次调用分裂为两条记录（带 id parsing 残留 + 无 id
            #   running→done），面板同一工具显示两行（✔ done + ◌ parsing
            #   残留）。降级阶段限定为 parsing：parsing 记录尚未被任何 start
            #   认领（属"等待开始"的流式解析），无 id 的 start 认领它是同一
            #   调用的开始信号；running 记录已被某次 start 认领（可能属另一
            #   条独立调用），认领会破坏记录隔离（既有测试
            #   test_done_tool_with_tool_id_fallback_keeps_other_record 锁定）。
            if rec is None and not tool_id:
                rec = self._find_record(
                    slot, tool_name, tool_id, ("parsing",),
                    fallback=True,
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
            # ★ P2（review 2026-08-22）：usage 值可能为 None（键存在但值为
            #   None）——``usage.get("input", 0)`` 在键值为 None 时不返回默认 0
            #   而是 None，``+= None`` 抛 TypeError；异常被 EventBus 吞掉后整条
            #   usage 更新丢失（面板 token 统计静默停滞）。与下方 speed 的
            #   float() 防御一致，统一用 ``or 0`` 兜底。
            def _i(k):
                return usage.get(k) or 0
            if replace:
                slot.input_tokens = _i("input")
                slot.output_tokens = _i("output")
                slot.live_input_tokens = _i("live_input")
                slot.live_output_tokens = _i("live_output")
            else:
                slot.input_tokens += _i("input")
                slot.output_tokens += _i("output")
                slot.live_input_tokens += _i("live_input")
                slot.live_output_tokens += _i("live_output")
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
