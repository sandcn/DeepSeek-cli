"""UI 显示层事件类型 — 不可变数据类

所有事件均为 frozen dataclass，确保线程安全。
事件类型按功能域分组：
- Lifecycle: 会话生命周期
- Tool: 工具调用（解析→开始→完成）
- Agent: Agent 注册与状态
- Model: 模型阶段与用量
- Output: 通用输出
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ── 基础事件 ────────────────────────────────────────────


@dataclass(frozen=True)
class DisplayEvent:
    """所有显示事件的基类。

    Attributes:
        timestamp: 事件创建时间戳（time.time）
        source: 事件来源标识，如 "agent", "subagent-1", "parallel", "tool-executor"
    """
    timestamp: float = field(default_factory=time.time)
    source: str = ""


# ── 生命周期 ────────────────────────────────────────────


@dataclass(frozen=True)
class SessionStarted(DisplayEvent):
    """会话开始。"""


@dataclass(frozen=True)
class SessionStopped(DisplayEvent):
    """会话停止。

    Attributes:
        final: 是否永久关闭（True=不再复用, False=可能暂停）
    """
    final: bool = False


# ── 工具调用 ────────────────────────────────────────────


@dataclass(frozen=True)
class ToolParsingEvent(DisplayEvent):
    """工具调用解析中 — 模型返回工具调用请求，正在解析参数。

    Attributes:
        label: 工具标识（tool_call_id）或 Agent 标识
        tool_name: 正在解析的工具名称
        arguments: 当前已累积的工具参数字符串（流式解析中的部分参数）
        tool_id: 工具调用唯一 ID（tool_call_id），用于前端精确匹配
    """
    label: str = ""
    tool_name: str = ""
    arguments: str = ""
    tool_id: str = ""


@dataclass(frozen=True)
class ToolStartedEvent(DisplayEvent):
    """工具开始执行。

    Attributes:
        label: 工具标识（tool_call_id）或 Agent 标识
        tool_name: 正在执行的工具名称
        detail: 执行详情说明（如参数摘要）
        metadata: 附加元数据（可选），如 {"参数": "120t", "解析": "0.5s"}
        tool_id: 工具调用唯一 ID（tool_call_id），用于前端精确匹配
    """
    label: str = ""
    tool_name: str = ""
    detail: str = ""
    metadata: Optional[Dict[str, Any]] = None
    tool_id: str = ""


@dataclass(frozen=True)
class ToolDoneEvent(DisplayEvent):
    """工具执行完成。

    Attributes:
        label: 工具标识（tool_call_id）或 Agent 标识
        tool_name: 已完成的工具名称
        success: 是否执行成功
        metadata: 附加元数据（可选），如 {"参数": "120t", "输出": "500t", "行数": 10}
        tool_id: 工具调用唯一 ID（tool_call_id），用于前端精确匹配
    """
    label: str = ""
    tool_name: str = ""
    success: bool = True
    metadata: Optional[Dict[str, Any]] = None
    tool_id: str = ""


@dataclass(frozen=True)
class ToolOutputChunkEvent(DisplayEvent):
    """工具执行过程中的实时输出块。

    在工具执行期间，每收到 stdout 输出行就发布此事件，
    前端可动态追加到工具气泡中。

    Attributes:
        label: 工具标识（tool_call_id）
        text: 输出文本块
        tool_id: 工具调用唯一 ID（tool_call_id），用于前端精确匹配
    """
    label: str = ""
    text: str = ""
    tool_id: str = ""


@dataclass(frozen=True)
class ToolBatchStartedEvent(DisplayEvent):
    """批量工具开始执行（多个工具并行/顺序执行）。

    Attributes:
        label: Agent 标识
        tool_names: 本次批次中的所有工具名称列表
    """
    label: str = ""
    tool_names: tuple[str, ...] = ()


# ── Agent 状态 ──────────────────────────────────────────


@dataclass(frozen=True)
class AgentAddedEvent(DisplayEvent):
    """新 Agent 注册。

    Attributes:
        label: Agent 唯一标识
        description: Agent 描述（显示用）
        status: 初始状态，默认 "running"
        dispatch_label: 所属 dispatch_agent 工具的 label（用于前端路由到正确容器）
    """
    label: str = ""
    description: str = ""
    status: str = "running"
    dispatch_label: str = ""
    agent_type: str = "execute"


@dataclass(frozen=True)
class AgentStatusChanged(DisplayEvent):
    """Agent 状态变更。

    Attributes:
        label: Agent 标识
        status: 新状态值（"running", "done", "fail", "error"）
    """
    label: str = ""
    status: str = ""


# ── 模型阶段 ────────────────────────────────────────────


@dataclass(frozen=True)
class ModelPhaseEvent(DisplayEvent):
    """模型推理阶段变更。

    Attributes:
        label: Agent 标识
        phase: 阶段名称（"thinking", "generating", "tool_call", "parsing",
               "answering", "batch", "error", "" 等）
        info: 阶段附加信息
    """
    label: str = ""
    phase: str = ""
    info: str = ""


@dataclass(frozen=True)
class PhaseDoneEvent(DisplayEvent):
    """模型阶段结束 — 推理/回答/工具调用阶段切换前发送。

    Attributes:
        label: Agent 标识
        phase: 已结束的阶段名称（"reasoning", "content"）
    """
    label: str = ""
    phase: str = ""


@dataclass(frozen=True)
class UsageUpdatedEvent(DisplayEvent):
    """Token 使用量更新。

    Attributes:
        label: Agent 标识
        usage: 用量字典，通常含 "input", "output", "speed" 等字段
        replace: True=覆盖已有用量, False=累加
    """
    label: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    replace: bool = False


# ── 流式内容事件（纯 Web 路径，取代 stdout 拦截） ──────

@dataclass(frozen=True)
class ContentChunkEvent(DisplayEvent):
    """内容流式块 — 模型生成的 content delta。

    Attributes:
        text: 当前 delta 文本片段
        label: Agent 标识
    """
    text: str = ""
    label: str = ""


@dataclass(frozen=True)
class ReasoningChunkEvent(DisplayEvent):
    """推理内容流式块 — 模型生成的 reasoning_content delta。

    Attributes:
        text: 当前 delta 文本片段
        label: Agent 标识
    """
    text: str = ""
    label: str = ""


# ── 附加状态（并行显示专用） ────────────────────────────

@dataclass(frozen=True)
class ParseInfoEvent(DisplayEvent):
    """解析信息更新（工具调用解析完成的统计信息）。

    Attributes:
        label: Agent 标识
        tool_names: 已解析的工具名称列表
        tokens: 解析消耗的 token 数
        elapsed: 解析耗时（秒）
    """
    label: str = ""
    tool_names: str = ""
    tokens: int = 0
    elapsed: float = 0.0


@dataclass(frozen=True)
class ParseInfoDoneEvent(DisplayEvent):
    """解析信息完成 — 工具参数接收完毕。

    在 ParseInfoEvent 流结束、最终 update_parse_info 之后发布。
    ChatUI 消费此事件以清除进度行并换行。

    Attributes:
        label: Agent 标识
    """
    label: str = ""


@dataclass(frozen=True)
class MetricsUpdateEvent(DisplayEvent):
    """统一的指标更新事件 — 合并 TokenEvent/LiveOutputEvent/LiveInputEvent/SpeedUpdatedEvent（已移除）。

    将所有实时指标合并到一个事件类型中，减少事件类型数量。
    发布时仅设置非默认值的字段，消费者按需读取。

    Attributes:
        label: Agent 标识
        output_tokens: 输出 token 数量（0 表示未变更）
        live_output_tokens: 实时输出 token 增量
        live_input_tokens: 实时输入 token 增量
        speed: 生成速度（tokens/s）
    """
    label: str = ""
    output_tokens: int = 0
    live_output_tokens: int = 0
    live_input_tokens: int = 0
    speed: float = 0.0


# ── 通用输出 ────────────────────────────────────────────


@dataclass(frozen=True)
class OutputEvent(DisplayEvent):
    """通用文本输出（替代直接 print）。

    Attributes:
        text: 输出文本
        level: 输出级别（"info", "success", "warning", "error", "raw"）
    """
    text: str = ""
    level: str = "info"


@dataclass(frozen=True)
class ToolSummaryEvent(DisplayEvent):
    """工具执行汇总（一轮中所有工具的结果摘要）。

    Attributes:
        successful_tools: 成功执行的工具名称列表
        failed_tools: 失败的工具列表 [(name, error), ...]
    """
    successful_tools: tuple[str, ...] = ()
    failed_tools: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class UserSelectNeededEvent(DisplayEvent):
    """用户选择需要 — Web UI 需要展示选择界面并等待用户响应。

    Attributes:
        select_id: 唯一标识这次选择请求
        title: 选择界面的标题
        options: 可供选择的选项列表
        multi_select: 是否允许多选
        default_options: 默认选中的选项
        timeout: 超时时间（秒）
    """
    select_id: str = ""
    title: str = ""
    options: tuple[str, ...] = ()
    multi_select: bool = False
    default_options: tuple[str, ...] = ()
    timeout: int = 120
    option_descriptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubagentPromptEvent(DisplayEvent):
    """子代理提词 — subagent 开始执行前发布，包含完整提词 markdown 文本。

    Attributes:
        label: Agent 标识（如 "agent-1"）
        description: Agent 描述（如 "解析 user.py 模块"）
        prompt: 提词 markdown 文本（纯文本，不含 ANSI）
        agent_type: Agent 类型（"execute" 等，用于缩写显示）
        index: 1 基序号（显示标题前缀，如 "1. [ex] 描述"）
    """
    label: str = ""
    description: str = ""
    prompt: str = ""
    agent_type: str = "execute"
    index: int = 0


@dataclass(frozen=True)
class AgentResultEvent(DisplayEvent):
    """子代理执行结果 — subagent 完成时发布，包含完整结果文本。

    Attributes:
        label: Agent 标识（如 "agent-1"）
        description: Agent 描述（如 "解析 user.py 模块"）
        result: 执行结果文本（成功时）
        error: 错误信息（失败时；成功时为空字符串）
        agent_type: Agent 类型（"execute" 等，用于缩写显示）
        index: 1 基序号（显示标题前缀，如 "1. [ex] 描述"）
    """
    label: str = ""
    description: str = ""
    result: str = ""
    error: str = ""
    agent_type: str = "execute"
    index: int = 0


# ── 事件类型注册表 ──────────────────────────────────────

# 所有事件类型的集合，用于 EventBus 按类型过滤订阅
ALL_EVENT_TYPES: tuple = (
    SessionStarted, SessionStopped,
    ToolParsingEvent, ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent, ToolBatchStartedEvent,
    AgentAddedEvent, AgentStatusChanged,
    ModelPhaseEvent, PhaseDoneEvent, UsageUpdatedEvent,
    ContentChunkEvent, ReasoningChunkEvent,
    ParseInfoEvent, ParseInfoDoneEvent, MetricsUpdateEvent,
    OutputEvent, ToolSummaryEvent,
    UserSelectNeededEvent,
    SubagentPromptEvent,
    AgentResultEvent,
)
