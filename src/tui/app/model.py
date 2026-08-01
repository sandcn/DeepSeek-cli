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
    #: 块是否已关闭（不再追加行）。仅连续的已关闭块可提交到增量缓存。
    closed: bool = False
    #: 已提交到缓存的行数（开放块随段落闭合增量提交 → 每帧只处理未提交尾）。
    committed_line_count: int = 0


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
        # ★ 增量渲染缓存：已关闭（提交）块的渲染行。
        #   静态历史只渲染一次并缓存，每帧不重建 → 大历史下渲染 O(live+新增)。
        self.committed_lines: list = []
        self.committed_count: int = 0
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
        # 每工具 box 跟踪（tool_id → 开放 box）
        self.tool_boxes: dict = {}
        self._current_tool_box: ChatBlock | None = None
        self._tool_id_seq: int = 0
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
        """追加聊天块（不自动提交，供流式累积）。"""
        block = ChatBlock(kind, list(lines) if lines else [])
        self.blocks.append(block)
        return block

    def append_committed(self, kind: str, lines) -> ChatBlock:
        """追加一个立即提交（关闭）的块：渲染缓存 + 块列表。"""
        block = self.append_block(kind, lines)
        block.closed = True
        self.commit_block(len(self.blocks) - 1)
        return block

    def commit_open_block(self, block: ChatBlock) -> None:
        """增量提交开放块的已闭合行（流式内容随段落闭合提交）。

        开放块（content/reasoning/tool）的闭段行立即进入缓存，块内只留
        未闭合尾（当前段落）→ 每帧渲染成本 O(live+当前段落)，不随响应增长。
        """
        if block.committed_line_count >= len(block.lines):
            return
        self.committed_lines.extend(
            self._block_to_ink_lines(block, block.committed_line_count)
        )
        block.committed_line_count = len(block.lines)

    def commit_block(self, index: int) -> None:
        """提交 blocks[committed_count..index] 到增量渲染缓存。

        仅提交**连续的已关闭**块——前面若有未关闭块（如流式内容块）则停止，
        避免跳过开放块导致其后续行丢失。已增量提交的行（committed_line_count）
        不再重复渲染。
        """
        while self.committed_count <= index and self.committed_count < len(self.blocks):
            block = self.blocks[self.committed_count]
            if not block.closed:
                break
            if block.committed_line_count < len(block.lines):
                self.committed_lines.extend(
                    self._block_to_ink_lines(block, block.committed_line_count)
                )
                block.committed_line_count = len(block.lines)
            self.committed_count += 1

    @staticmethod
    def _block_to_ink_lines(block, start: int = 0):
        """将块内 AnsiLine（从 start 起）转为 ink Line（推理块叠加 dim/italic）。"""
        from src.tui.ink import Line, StyledRun
        from src.renderer.ansi.style import Style as _AnsiStyle
        slice_lines = block.lines[start:]
        if not slice_lines:
            return []
        reasoning_style = (
            _AnsiStyle(dim=True, italic=True) if block.kind == "reasoning" else None
        )
        out: list = []
        for ansi_line in slice_lines:
            runs = []
            for r in ansi_line.runs:
                if not r.text:
                    continue
                st = r.style
                if reasoning_style is not None:
                    st = reasoning_style if st is None else st.merge(reasoning_style)
                runs.append(StyledRun(r.text, st))
            out.append(Line(runs))
        return out

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
        # 提交到增量渲染缓存
        if 0 <= self.reasoning_block_index < len(self.blocks):
            self.blocks[self.reasoning_block_index].closed = True
            self.commit_block(self.reasoning_block_index)

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
        # 提交到增量渲染缓存
        if 0 <= self.content_block_index < len(self.blocks):
            self.blocks[self.content_block_index].closed = True
            self.commit_block(self.content_block_index)

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

    # ── 工具 box（每工具一个，增量刷新） ────────────

    def open_tool_box(self, tool_id: str, tool_name: str, detail: str = "") -> ChatBlock:
        """打开一个工具 box：标题行立即显示，输出增量追加。"""
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        from src.tools.registry import get_tool_display_name
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        block = self.append_block("tool")
        block.extra["tool_id"] = tool_id or ""
        block.extra["tool_name"] = tool_name
        title = f"  \u250c\u2500 {display} \u2500\u2510"
        if detail:
            title = f"  \u250c\u2500 {display} \u00b7 {detail} \u2500\u2510"
        block.lines.append(AnsiLine.of(title, Style(fg=23, bold=True)))
        self._current_tool_box = block
        self.tool_boxes[tool_id or self._next_tool_id()] = block
        self.commit_open_block(block)  # 标题立即上屏
        return block

    def append_tool_output(self, tool_id: str, text: str) -> None:
        """追加工具输出行到对应 box（增量提交）。"""
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        block = self.tool_boxes.get(tool_id) or self._current_tool_box
        if block is None:
            block = self.open_tool_box(tool_id, "")
        line = AnsiLine.of("  \u2502 ", Style(fg=242))
        for seg in text.split("\n"):
            l = AnsiLine.of("  \u2502 ", Style(fg=242))
            l.append(seg)
            block.lines.append(l)
        self.commit_open_block(block)

    def close_tool_box(self, tool_id: str, success: bool) -> None:
        """关闭工具 box：追加状态底行并提交。"""
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        block = self.tool_boxes.pop(tool_id, None) or self._current_tool_box
        if block is None:
            return
        status = "\u2714" if success else "\u2716"
        block.lines.append(AnsiLine.of(
            f"  \u2570\u2500 {status} \u2500\u2518",
            Style(fg=41 if success else 196),
        ))
        block.closed = True
        self.commit_block(len(self.blocks) - 1)
        if self._current_tool_box is block:
            self._current_tool_box = None

    def _next_tool_id(self) -> str:
        self._tool_id_seq += 1
        return f"tool-{self._tool_id_seq}"

    # 兼容旧字段（open_tool_group/close_tool_group 由 per-tool box 取代）
    def open_tool_group(self) -> ChatBlock:
        return self.open_tool_box("", "")

    def close_tool_group(self) -> None:
        if self._current_tool_box is not None:
            self.close_tool_box("", True)


__all__ = [
    "AppModel",
    "ChatBlock",
    "CompletionState",
    "StatusState",
    "ReasoningState",
]
