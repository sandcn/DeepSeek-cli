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
    kind: int = 0


@dataclass(frozen=True)
class CmdContent:
    """助手回答块 — 对应 RenderCommand.CONTENT (1)。"""
    text: str
    kind: int = 1


@dataclass(frozen=True)
class CmdPhaseDone:
    """阶段完成 — 对应 RenderCommand.PHASE_DONE (2)。"""
    phase: str
    kind: int = 2


@dataclass(frozen=True)
class CmdToolOutput:
    """工具输出内容 — 对应 RenderCommand.TOOL_OUTPUT (6)。"""
    text: str
    kind: int = 6


@dataclass(frozen=True)
class CmdToolSummary:
    """工具汇总块 — 对应 RenderCommand.TOOL_SUMMARY (7)。"""
    successful: tuple[str, ...]
    failed: tuple[str, ...]
    kind: int = 7


@dataclass(frozen=True)
class CmdUserMsg:
    """用户消息 — 对应 RenderCommand.USER_MSG (8)。"""
    text: str
    kind: int = 8


@dataclass(frozen=True)
class CmdParseInfo:
    """解析进度信息 — 对应 RenderCommand.PARSE_INFO (9)。"""
    tool_names: str
    tokens: Any   # int | _CLEAR_PARSE_LINE (-1)
    elapsed: float
    kind: int = 9


@dataclass(frozen=True)
class CmdNotification:
    """通知消息 — 对应 RenderCommand.NOTIFICATION (11)。"""
    text: str
    kind: int = 11


@dataclass(frozen=True)
class CmdWriteLine:
    """样式化行输出 — 对应 RenderCommand.WRITE_LINE (12)。"""
    text: str
    kind: int = 12


@dataclass(frozen=True)
class CmdDisplayMsgs:
    """显示消息列表 — 对应 RenderCommand.DISPLAY_MSGS (13)。"""
    messages: list[dict]
    speed: int
    kind: int = 13


@dataclass(frozen=True)
class CmdToolCountInc:
    """工具计数+1 — 对应 RenderCommand.TOOL_COUNT_INC (14)。"""
    kind: int = 14


@dataclass(frozen=True)
class CmdToolFailInc:
    """工具失败计数+1 — 对应 RenderCommand.TOOL_FAIL_INC (15)。"""
    kind: int = 15


@dataclass(frozen=True)
class CmdError:
    """系统错误 — 对应 RenderCommand.ERROR (16)。"""
    message: str
    kind: int = 16


@dataclass(frozen=True)
class CmdToolCountDec:
    """工具计数-1 — 对应 RenderCommand.TOOL_COUNT_DEC (17)。"""
    kind: int = 17


@dataclass(frozen=True)
class CmdSubagentFrame:
    """SubAgent 面板帧 — 对应 RenderCommand.SUBAGENT_FRAME (18)。"""
    frame_lines: tuple
    kind: int = 18


@dataclass(frozen=True)
class CmdInputChanged:
    """输入文本变更 — 用户按键导致输入缓冲区变化。"""
    text: str
    cursor_pos: int
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
    kind: int = 20


@dataclass(frozen=True)
class CmdAnimationTick:
    """动画时钟滴答 — 驱动 AnimationClock._tick() 更新所有注册动画状态。

    由 AnimationClock 定时器通过 TuiEngine.push_cmd() 入队，
    在 render 线程中处理，避免竞态条件。
    """
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
    """
    tool_id: str
    name: str
    status: str = "running"  # "running" / "completed" / "failed"
    text: str = ""
    kind: int = 22
