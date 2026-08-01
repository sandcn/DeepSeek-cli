"""AppModel — 聊天 UI 应用模型。

单一真源：RenderCmd → AppModel 状态变更（apply.py），
组件树读取 AppModel 渲染。替代 ChatRenderState + _BottomBar 状态域。

块列表：每个聊天块 = ChatBlock（kind + AnsiLine 行列表）。
推理/内容通道：AnsiStreamRenderer 流式累积，PhaseDone 关闭后固化到块。
阶段状态机：推理 INACTIVE/ACTIVE/CLOSED + content 关闭标志（多轮重开）。
"""

from __future__ import annotations

import time
from src._compat import dataclass
from dataclasses import field
from enum import Enum
from typing import Any


class ReasoningState(Enum):
    """推理通道状态机（与 _ReasoningState 等价语义）。"""

    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass
class ChatBlock:
    """聊天块 — 一组已渲染行。

    Attributes:
        kind: 块类型（reasoning/content/user/tool/notification/error/
            write_line/splash/subagent/parse_info/separator）。
        lines: 已渲染的 AnsiLine 列表。
        extra: 附加数据（如工具调用组状态）。
    """

    kind: str
    lines: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class CompletionState:
    """补全弹窗状态（_CmplHandler 注入）。"""

    visible: bool = False
    title: str = "补全"
    items: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    selected: int = 0
    start_pos: int = 0
    orig_prefix: str = ""
    types: list = field(default_factory=list)
    match_prefix: str = ""
    popup_height: int = 0


@dataclass
class StatusState:
    """状态栏数据（移植 BottomBarStatus 状态域）。"""

    model_name: str = ""
    tool_count: int = 0
    tool_fail: int = 0
    tool_total: int = 0
    main_phase: str = ""
    main_phase_start: float = 0.0
    tool_phase_start: float = 0.0
    status_active: bool = False
    cpu: int = 0
    mem: int = 0


class AppModel:
    """聊天 UI 应用模型。"""

    def __init__(self) -> None:
        # ── 聊天块 ──
        self.blocks: list[ChatBlock] = []
        # 推理/内容通道（AnsiStreamRenderer 惰性创建）
        self.reasoning_renderer: Any = None
        self.content_renderer: Any = None
        self.reasoning_state: ReasoningState = ReasoningState.INACTIVE
        self.content_closed: bool = False
        self.reasoning_block_index: int = -1
        self.content_block_index: int = -1
        # 工具调用组
        self.in_tool_group: bool = False
        self.tool_block_index: int = -1
        # 状态栏
        self.status: StatusState = StatusState()
        # 输入
        self.input_text: str = ""
        self.input_cursor: int = 0
        # 补全
        self.completion: CompletionState = CompletionState()
        # 实时解析进度行（同位置刷新；ParseInfoDone 后提交并清空）
        self.parse_line: Any = None
        # subagent 面板行（控制器推送）
        self.subagent_lines: list = []

    # ── 块管理 ──────────────────────────────────────

    def append_block(self, kind: str, lines=None) -> ChatBlock:
        block = ChatBlock(kind, list(lines) if lines else [])
        self.blocks.append(block)
        return block

    # ── 推理/内容通道 ───────────────────────────────

    def ensure_reasoning(self):
        """确保推理通道开启（返回渲染器，None 表示已关闭）。"""
        if self.reasoning_state == ReasoningState.CLOSED:
            return None
        if self.reasoning_renderer is None:
            from src.renderer.ansi import AnsiStreamRenderer
            self.reasoning_renderer = AnsiStreamRenderer()
            self.reasoning_state = ReasoningState.ACTIVE
            self.reasoning_block_index = len(self.blocks)
            self.append_block("reasoning")
        return self.reasoning_renderer

    def close_reasoning(self) -> None:
        """关闭推理通道：固化渲染器输出 + 分隔线。"""
        if self.reasoning_state == ReasoningState.CLOSED:
            return
        rr = self.reasoning_renderer
        if rr is not None:
            rr.close()
            lines = rr.take_lines()
            if 0 <= self.reasoning_block_index < len(self.blocks):
                block = self.blocks[self.reasoning_block_index]
                block.lines.extend(lines)
                from src.tui.core.style import Style
                from src.renderer.ansi.helpers import AnsiLine
                block.lines.append(AnsiLine.of("  " + "\u2500" * 40, Style(fg=240)))
            self.reasoning_renderer = None
        self.reasoning_state = ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        """重新打开推理通道（CLOSED → INACTIVE）。"""
        if self.reasoning_state != ReasoningState.CLOSED:
            return
        self.reasoning_renderer = None
        self.reasoning_state = ReasoningState.INACTIVE

    def ensure_content(self):
        """确保内容通道开启（None 表示已关闭）。"""
        if self.content_closed:
            return None
        if self.content_renderer is None:
            from src.renderer.ansi import AnsiStreamRenderer
            self.content_renderer = AnsiStreamRenderer()
            self.content_block_index = len(self.blocks)
            self.append_block("content")
        return self.content_renderer

    def close_content(self) -> None:
        """关闭内容通道：固化渲染器输出。"""
        cr = self.content_renderer
        if cr is not None:
            cr.close()
            lines = cr.take_lines()
            if 0 <= self.content_block_index < len(self.blocks):
                self.blocks[self.content_block_index].lines.extend(lines)
            self.content_renderer = None
        self.content_closed = True

    def reopen_content(self) -> None:
        """重新打开内容通道（多轮会话新一轮内容前调用）。"""
        self.content_closed = False

    def flush_open_channels(self) -> None:
        """停止时固化所有开放通道。"""
        try:
            self.close_reasoning()
        except Exception:
            pass
        try:
            self.close_content()
        except Exception:
            pass

    # ── 工具组 ──────────────────────────────────────

    def open_tool_group(self) -> ChatBlock:
        """打开工具调用组（返回工具块）。"""
        if not self.in_tool_group:
            self.in_tool_group = True
            self.tool_block_index = len(self.blocks)
            return self.append_block("tool")
        return self.blocks[self.tool_block_index]

    def close_tool_group(self) -> None:
        self.in_tool_group = False
        self.tool_block_index = -1


__all__ = [
    "AppModel",
    "ChatBlock",
    "CompletionState",
    "StatusState",
    "ReasoningState",
]
