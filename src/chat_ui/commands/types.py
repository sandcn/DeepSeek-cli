"""RenderCommand frozen dataclass 层次结构 — 替代 IntEnum + tuple。

每个命令类型对应一个 frozen dataclass，提供类型安全的字段访问。
通过 isinstance / match-case 多态分发放取代 _RENDER_DISPATCH 字典 + getattr 反射。

使用方式:
    # 构造
    cmd = CmdContent(text="hello")
    # 分发
    match cmd:
        case CmdContent(text=t):
            handle_content(t)

Transition: RenderCommand IntEnum 值保留作 kind 属性，供过渡期兼容。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CmdReasoning:
    """推理内容块 — 对应 RenderCommand.REASONING (0)。"""
    text: str
    layer: int = 10
    kind: int = 0


@dataclass(frozen=True)
class CmdContent:
    """助手回答块 — 对应 RenderCommand.CONTENT (1)。"""
    text: str
    layer: int = 10
    kind: int = 1


@dataclass(frozen=True)
class CmdPhaseDone:
    """阶段完成 — 对应 RenderCommand.PHASE_DONE (2)。"""
    phase: str
    layer: int = 10
    kind: int = 2


@dataclass(frozen=True)
class CmdToolOutput:
    """工具输出内容 — 对应 RenderCommand.TOOL_OUTPUT (6)。"""
    text: str
    layer: int = 10
    kind: int = 6


@dataclass(frozen=True)
class CmdToolSummary:
    """工具汇总块 — 对应 RenderCommand.TOOL_SUMMARY (7)。"""
    successful: tuple[str, ...]
    failed: tuple[str, ...]
    layer: int = 10
    kind: int = 7


@dataclass(frozen=True)
class CmdUserMsg:
    """用户消息 — 对应 RenderCommand.USER_MSG (8)。"""
    text: str
    layer: int = 10
    kind: int = 8


@dataclass(frozen=True)
class CmdParseInfo:
    """解析进度信息 — 对应 RenderCommand.PARSE_INFO (9)。"""
    tool_names: str
    tokens: Any   # int | _CLEAR_PARSE_LINE (-1)
    elapsed: float
    layer: int = 10
    kind: int = 9


@dataclass(frozen=True)
class CmdNotification:
    """通知消息 — 对应 RenderCommand.NOTIFICATION (11)。"""
    text: str
    layer: int = 20
    kind: int = 11


@dataclass(frozen=True)
class CmdWriteLine:
    """样式化行输出 — 对应 RenderCommand.WRITE_LINE (12)。"""
    text: str
    layer: int = 10
    kind: int = 12


@dataclass(frozen=True)
class CmdDisplayMsgs:
    """显示消息列表 — 对应 RenderCommand.DISPLAY_MSGS (13)。"""
    messages: list[dict]
    speed: int
    layer: int = 10
    kind: int = 13


@dataclass(frozen=True)
class CmdToolCountInc:
    """工具计数+1 — 对应 RenderCommand.TOOL_COUNT_INC (14)。"""
    layer: int = 10
    kind: int = 14


@dataclass(frozen=True)
class CmdToolFailInc:
    """工具失败计数+1 — 对应 RenderCommand.TOOL_FAIL_INC (15)。"""
    layer: int = 10
    kind: int = 15


@dataclass(frozen=True)
class CmdError:
    """系统错误 — 对应 RenderCommand.ERROR (16)。"""
    message: str
    layer: int = 20
    kind: int = 16


@dataclass(frozen=True)
class CmdToolCountDec:
    """工具计数-1 — 对应 RenderCommand.TOOL_COUNT_DEC (17)。"""
    layer: int = 10
    kind: int = 17


# @deprecated: 由 VNode 内联渲染替代。subagent_slots 数据通过 CmdSubagentSlotUpdate + TuiState.subagent_slots 传递。
@dataclass(frozen=True)
class CmdSubagentFrame:
    """SubAgent 面板帧 — 对应 RenderCommand.SUBAGENT_FRAME (18)。

    @deprecated: 已废弃，由 CmdSubagentSlotUpdate + VNode 内联渲染替代。
    保留类定义避免 import 报错，实际不再被 VNode 渲染路径消费。
    """
    frame_lines: tuple
    layer: int = 10
    kind: int = 18


@dataclass(frozen=True)
class CmdInputChanged:
    """输入文本变更 — 用户按键导致输入缓冲区变化。"""
    text: str
    cursor_pos: int
    layer: int = 10
    kind: int = 19


@dataclass(frozen=True)
class CmdStatusUpdate:
    """状态行更新 — 模型名/tokens/时间/工具计数等 BottomBar 状态变化。

    所有字段默认 None 表示「未提供」（保留旧值），
    显式传入非 None 值表示「更新为该值」（包括 0 / 0.0 / ""）。
    """
    model: str | None = None
    tokens: int | None = None
    elapsed: float | None = None
    tool_count: int | None = None
    tool_fail: int | None = None
    streaming: bool = False
    layer: int = 10
    kind: int = 20


@dataclass(frozen=True)
class CmdAnimationTick:
    """动画时钟滴答 — 驱动 AnimationClock._tick() 更新所有注册动画状态。

    由 AnimationClock 定时器通过 TuiEngine.push_cmd() 入队，
    在 render 线程中处理，避免竞态条件。
    """
    layer: int = 10
    kind: int = 21


@dataclass(frozen=True)
class CmdToolCallUpdate:
    """工具调用状态更新命令。

    由 Agent 工具调用生命周期事件触发，通过 TuiRenderer._do_tool_call_update()
    渲染为带状态图标的单行输出（running→⚙ / completed→✓ / failed→✗）。

    Attributes:
        tool_id: 工具调用的唯一标识符（用于去重/关联）
        name: 工具名称（如 "read_file"、"bash"）
        status: 状态 — "running" / "completed" / "failed"
        text: 附加文本（工具输出摘要，可选）
        params_summary: 工具参数摘要（如 "src/main.py"），Claude 风格卡片使用
        elapsed_ms: 工具调用耗时（毫秒），Claude 风格卡片使用
    """
    tool_id: str
    name: str
    status: str = "running"  # "running" / "completed" / "failed"
    text: str = ""
    params_summary: str = ""
    elapsed_ms: float = 0.0
    layer: int = 10
    kind: int = 22


@dataclass(frozen=True)
class CmdSubagentSlotUpdate:
    """SubAgent 状态槽位更新 — 将 AgentStateStore 状态同步到 TuiState。

    由 ParallelDisplay 在 update_* 方法中推送，TuiStore 通过
    _reduce_subagent_slot_update reducer 合并到 subagent_slots dict。

    Attributes:
        label: Agent 标识名（如 "agent-1"）
        slot: Agent 槽位数据（可序列化 dict，字段与 AgentSlot 对齐）
    """
    label: str
    slot: dict
    layer: int = 10
    kind: int = 23
