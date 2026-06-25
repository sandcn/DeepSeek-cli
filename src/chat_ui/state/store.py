"""TuiStore + TuiState — 声明式不可变状态管理。

TuiState 是完整 TUI 状态快照（frozen dataclass），所有渲染命令 dispatch 到此状态，
产出新不可变快照。TuiStore 管理状态转换，通过纯函数 reducer 实现可预测的状态更新。

使用方式:
    store = TuiStore()
    store.dispatch(CmdContent(text="hello"))
    state = store.get_state()  # TuiState 实例

设计:
    - TuiState 使用 dataclass(frozen=True) + field(default_factory=...) 避免可变默认值
    - reducer 纯函数 (TuiState, Cmd*) → TuiState，使用 dataclasses.replace 实现不可变更新
    - _ACTION_TYPE_NAMES 映射表供 _vnode_builder.py 使用
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

from ..commands.types import (
    CmdAnimationTick,
    CmdContent,
    CmdDisplayMsgs,
    CmdError,
    CmdInputChanged,
    CmdNotification,
    CmdParseInfo,
    CmdPhaseDone,
    CmdReasoning,
    CmdStatusUpdate,
    CmdSubagentSlotUpdate,
    CmdToolCallUpdate,
    CmdToolCountDec,
    CmdToolCountInc,
    CmdToolFailInc,
    CmdToolOutput,
    CmdToolSummary,
    CmdUserMsg,
    CmdWriteLine,
)
from ..state.state_tree import CompletionPopup, InputLine, SelectionMenu, StatusLine


# ═══════════════════════════════════════════════════════════
# 步骤 6.1: TuiState frozen dataclass
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TuiState:
    """完整 TUI 状态快照 — 声明式渲染的单一数据源。

    所有渲染命令 dispatch 到此状态，产出新不可变快照。
    _vnode_builder.py 消费此快照构建 VNode 树。
    """
    # 版本号（供未来格式升级）
    version: int = 1

    # ── 内容区 ──
    reasoning_text: str = ""
    content_text: str = ""
    tool_outputs: list[tuple[str, str]] = field(default_factory=list)  # [(name, output), ...]
    notifications: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    write_lines: list[str] = field(default_factory=list)
    displayed_messages: list[dict[str, Any]] = field(default_factory=list)

    # ── 工具调用与结果 ──
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)  # [{"tool_id": str, "name": str, "status": str, "text": str, "params_summary": str, "elapsed_ms": float}, ...]

    # ── 底部栏 ──
    status: StatusLine = field(default_factory=StatusLine)
    input_line: InputLine = field(default_factory=InputLine)
    completion: CompletionPopup = field(default_factory=CompletionPopup)
    selection: SelectionMenu = field(default_factory=SelectionMenu)

    # ── 元数据 ──
    phase: str = ""  # "reasoning" | "content" | "tool" | ""
    parse_info: str = ""  # 当前解析信息文本
    tool_summary: tuple[tuple, tuple] = field(default_factory=lambda: ((), ()))  # (successful, failed)
    subagent_slots: dict = field(default_factory=dict)  # label → slot dict（合并自 AgentStateStore）

    # ── 工具 ──
    tool_count: int = 0
    tool_fail: int = 0

    # ── 动画 ──
    animation_frame: int = 0  # AnimationClock 驱动的全局帧计数


# ═══════════════════════════════════════════════════════════
# 步骤 6.3: Reducer 纯函数
# ═══════════════════════════════════════════════════════════

Reducer = Callable[[TuiState, Any], TuiState]


def _reduce_reasoning(state: TuiState, cmd: CmdReasoning) -> TuiState:
    return replace(state,
        reasoning_text=state.reasoning_text + cmd.text,
        phase="reasoning",
    )


def _reduce_content(state: TuiState, cmd: CmdContent) -> TuiState:
    return replace(state,
        content_text=state.content_text + cmd.text,
        phase="content",
    )


def _reduce_phase_done(state: TuiState, cmd: CmdPhaseDone) -> TuiState:
    return replace(state, phase="")


def _reduce_tool_output(state: TuiState, cmd: CmdToolOutput) -> TuiState:
    outputs = list(state.tool_outputs)
    # 若当前不在 tool 阶段（新工具开始），追加新条目而非合并到旧条目
    if outputs and state.phase == "tool":
        last_name, last_text = outputs[-1]
        # ★ 修复：拼接时确保有换行分隔，防止相邻 chunk 行粘连。
        #   EventDispatcher._on_tool_output 中去掉了 chunk 尾部 \n，
        #   若直接 last_text + cmd.text 会导致流式场景（每个 chunk 一行）
        #   所有输出行粘合在一起（如 "\n$ ls" + "file1" → "\n$ lsfile1"）。
        #   当 last_text 不以 \n 结尾且 cmd.text 不以 \n 开头时插入 \n 分隔。
        if (last_text and not last_text.endswith('\n')
                and not cmd.text.startswith('\n')):
            separator = '\n'
        else:
            separator = ''
        outputs[-1] = (last_name, last_text + separator + cmd.text)
    else:
        outputs.append(("", cmd.text))
    return replace(state, tool_outputs=outputs, phase="tool")


def _reduce_tool_summary(state: TuiState, cmd: CmdToolSummary) -> TuiState:
    return replace(state, tool_summary=(cmd.successful, cmd.failed))


def _reduce_user_msg(state: TuiState, cmd: CmdUserMsg) -> TuiState:
    """追加用户消息，同时清空 subagent_slots（新一轮对话开始）。"""
    msgs = list(state.user_messages)
    msgs.append(cmd.text)
    return replace(state, user_messages=msgs, subagent_slots={})


def _reduce_parse_info(state: TuiState, cmd: CmdParseInfo) -> TuiState:
    # CmdParseInfo 有 tool_names, tokens, elapsed
    if cmd.tokens == -1:  # _CLEAR_PARSE_LINE
        return replace(state, parse_info="")
    p = f"~ {cmd.tool_names} {cmd.tokens}t {cmd.elapsed:.2f}s" if cmd.tool_names else ""
    return replace(state, parse_info=p)


def _reduce_notification(state: TuiState, cmd: CmdNotification) -> TuiState:
    return replace(state, notifications=state.notifications + [cmd.text])


def _reduce_write_line(state: TuiState, cmd: CmdWriteLine) -> TuiState:
    return replace(state, write_lines=state.write_lines + [cmd.text])


def _reduce_display_msgs(state: TuiState, cmd: CmdDisplayMsgs) -> TuiState:
    return replace(state, displayed_messages=list(cmd.messages))


def _reduce_tool_count_inc(state: TuiState, cmd: CmdToolCountInc) -> TuiState:
    s = state.status
    return replace(state, status=replace(s, tool_count=s.tool_count + 1))


def _reduce_tool_count_dec(state: TuiState, cmd: CmdToolCountDec) -> TuiState:
    s = state.status
    return replace(state, status=replace(s, tool_count=max(0, s.tool_count - 1)))


def _reduce_tool_fail_inc(state: TuiState, cmd: CmdToolFailInc) -> TuiState:
    s = state.status
    return replace(state, status=replace(s, tool_fail=s.tool_fail + 1))


def _reduce_error(state: TuiState, cmd: CmdError) -> TuiState:
    return replace(state, errors=state.errors + [cmd.message])


def _reduce_subagent_slot_update(state: TuiState, cmd: CmdSubagentSlotUpdate) -> TuiState:
    """将 AgentStateStore 的 slot 数据合并到 TuiState.subagent_slots。

    以 label 为键：
    - cmd.slot 非空时 → 浅拷贝并添加/更新到 subagent_slots
    - cmd.slot 为空 {} 时 → 从 subagent_slots 中移除该 label 条目
    """
    slots = dict(state.subagent_slots)
    if cmd.slot:
        slots[cmd.label] = dict(cmd.slot)  # 浅拷贝 slot dict
    else:
        slots.pop(cmd.label, None)  # 移除条目（幂等）
    return replace(state, subagent_slots=slots)


def _reduce_input_changed(state: TuiState, cmd: CmdInputChanged) -> TuiState:
    """用户输入变更 reducer。"""
    new_input = replace(state.input_line, text=cmd.text, cursor_pos=cmd.cursor_pos)
    return replace(state, input_line=new_input)


def _reduce_status_update(state: TuiState, cmd: CmdStatusUpdate) -> TuiState:
    """状态行更新 reducer。

    使用 None 哨兵区分「未提供」与「零值」：
      - 字段为 None → 保留旧值
      - 字段非 None → 使用新值（包括 0 / 0.0 / ""）
    """
    new_status = replace(state.status,
        model=cmd.model if cmd.model is not None else state.status.model,
        tokens=cmd.tokens if cmd.tokens is not None else state.status.tokens,
        elapsed=cmd.elapsed if cmd.elapsed is not None else state.status.elapsed,
        tool_count=cmd.tool_count if cmd.tool_count is not None else state.status.tool_count,
        tool_fail=cmd.tool_fail if cmd.tool_fail is not None else state.status.tool_fail,
        streaming=cmd.streaming,
    )
    return replace(state, status=new_status)


def _reduce_tool_call_update(state: TuiState, cmd: CmdToolCallUpdate) -> TuiState:
    """工具调用状态更新 reducer — 根据 tool_id 更新或添加 tool_calls 条目。

    条目格式: {"tool_id": str, "name": str, "status": str, "text": str,
              "params_summary": str, "elapsed_ms": float}

    当 cmd.status 为 "completed" 或 "failed" 时，同步追加条目到
    tool_results 并从 tool_calls 中移除已完成条目。
    """
    new_calls = list(state.tool_calls)
    if cmd.status in ("completed", "failed"):
        new_results = list(state.tool_results)
        new_results.append({
            "tool_id": cmd.tool_id,
            "name": cmd.name,
            "status": cmd.status,
            "text": cmd.text,
            "params_summary": cmd.params_summary,
            "elapsed_ms": cmd.elapsed_ms,
        })
        # 从 tool_calls 中移除已完成的
        new_calls = [c for c in new_calls if c["tool_id"] != cmd.tool_id]
        return replace(state, tool_calls=new_calls, tool_results=new_results)

    # running: 更新或添加
    updated = False
    for i, call in enumerate(new_calls):
        if call.get("tool_id") == cmd.tool_id:
            new_calls[i] = {"tool_id": cmd.tool_id, "name": cmd.name,
                            "status": cmd.status, "text": cmd.text,
                            "params_summary": cmd.params_summary,
                            "elapsed_ms": cmd.elapsed_ms}
            updated = True
            break
    if not updated:
        new_calls.append({"tool_id": cmd.tool_id, "name": cmd.name,
                          "status": cmd.status, "text": cmd.text,
                          "params_summary": cmd.params_summary,
                          "elapsed_ms": cmd.elapsed_ms})
    return replace(state, tool_calls=new_calls)


def _reduce_animation_tick(state: TuiState, cmd: CmdAnimationTick) -> TuiState:
    """动画滴答 reducer — 递增全局动画帧计数。

    由 AnimationClock 定时器推送 CmdAnimationTick 驱动，
    每帧 +1。VNodeRenderStrategy 读取此计数判断是否需要重新渲染。
    """
    return replace(state, animation_frame=state.animation_frame + 1)


# ═══════════════════════════════════════════════════════════
# 步骤 6.2: TuiStore 不可变状态容器
# ═══════════════════════════════════════════════════════════

class TuiStore:
    """不可变状态容器。

    用法:
        store = TuiStore()
        store.dispatch(CmdContent(text="hello"))
        state = store.get_state()  # 新 TuiState 实例
    """

    def __init__(self, initial_state: TuiState | None = None):
        self._state = initial_state if initial_state is not None else TuiState()

        # 注册全部 reducer 纯函数
        self._reducers: dict[type, Reducer] = {
            CmdReasoning: _reduce_reasoning,
            CmdContent: _reduce_content,
            CmdPhaseDone: _reduce_phase_done,
            CmdToolOutput: _reduce_tool_output,
            CmdToolSummary: _reduce_tool_summary,
            CmdUserMsg: _reduce_user_msg,
            CmdParseInfo: _reduce_parse_info,
            CmdNotification: _reduce_notification,
            CmdWriteLine: _reduce_write_line,
            CmdDisplayMsgs: _reduce_display_msgs,
            CmdToolCountInc: _reduce_tool_count_inc,
            CmdToolCountDec: _reduce_tool_count_dec,
            CmdToolFailInc: _reduce_tool_fail_inc,
            CmdError: _reduce_error,
            CmdSubagentSlotUpdate: _reduce_subagent_slot_update,
            CmdInputChanged: _reduce_input_changed,
            CmdStatusUpdate: _reduce_status_update,
            CmdToolCallUpdate: _reduce_tool_call_update,
            CmdAnimationTick: _reduce_animation_tick,
        }

    def dispatch(self, action: Any) -> TuiState:
        """dispatch action，返回新 TuiState。

        纯函数风格：不修改 self._state，返回新实例并更新内部状态。
        """
        reducer = self._reducers.get(type(action))
        if reducer is not None:
            self._state = reducer(self._state, action)
        return self._state

    def get_state(self) -> TuiState:
        return self._state
