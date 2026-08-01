"""AppModel — 聊天 UI 应用模型。

单一真源：RenderCmd → AppModel 状态变更（apply.py），
组件树读取 AppModel 渲染。替代 ChatRenderState + _BottomBar 状态域。

块列表：每个聊天块 = ChatBlock（kind + AnsiLine 行列表）。
推理/内容通道：AnsiStreamRenderer 流式累积，PhaseDone 关闭后固化到块。
阶段状态机：推理 INACTIVE/ACTIVE/CLOSED + content 关闭标志（多轮重开）。
"""

from __future__ import annotations

import logging
import time
from src._compat import dataclass
from dataclasses import field
from enum import Enum
from typing import Any

_logger = logging.getLogger(__name__)


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
    #: 关闭块冻结行缓存（list[ink Line]，方向D 步骤15）。关闭时构建全块 ink
    #: Line（含工具状态图标），供 ``_block_styled_lines`` 复用 ``Line.runs``
    #: 引用（免每帧 Style merge）。None=未冻结（开放块/未关闭）。
    _cached_ink_lines: list | None = None


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


@dataclass
class HistorySearchState:
    """反向历史搜索状态（方向D 步骤14，Ctrl+R 配置门控）。

    Attributes:
        query: 搜索查询（进入搜索时的缓冲文本）。
        matches: 匹配历史列表（最近优先，history[0] 为最新）。
        index: 当前匹配索引（-1 表示无匹配）。
        active: 是否处于搜索模式（True 时 input-area 渲染搜索覆盖行）。
    """

    query: str = ""
    matches: list = field(default_factory=list)
    index: int = -1
    active: bool = False


def _tool_icon_runs(block) -> list:
    """工具块标题前置状态图标 runs（方向D 步骤15，渲染装饰）。

    不改动 ``block.lines`` 原文（模型层保持原始标题行，测试断言
    ``block.lines[0].plain.startswith("  · ")`` 依赖此不变式）。
    样式取 ``StyleSheet.resolve`` 语义色（success/error/warn），
    兜底硬编码确保任何加载顺序下都有默认值。

    Args:
        block: 工具块（ChatBlock.kind == "tool"）。

    Returns:
        StyledRun 列表（图标 + 空格），running ● / done ✔ / fail ✖。
    """
    from src.tui.ink import StyledRun
    from src.tui.core.style import Style, StyleSheet
    status = block.extra.get("tool_status", "running")
    if status == "done":
        return [StyledRun("\u2714 ", StyleSheet.resolve("success", Style(fg=41)))]
    if status == "fail":
        return [StyledRun("\u2716 ", StyleSheet.resolve("error", Style(fg=196, bold=True)))]
    return [StyledRun("\u25cf ", StyleSheet.resolve("warn", Style(fg=214)))]


def _default_config():
    """TuiConfig 默认实例（AppModel._config 为 None 时惰性获取）。"""
    from src.tui._config import TuiConfig
    return TuiConfig.defaults()


class AppModel:
    """聊天 UI 应用模型。"""

    def __init__(self, config: "TuiConfig | None" = None) -> None:
        # ── 配置（方向D 步骤15：工具卡片截断/折叠阈值；None=默认配置） ──
        self._config = config
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
        # 终端宽度（session 每帧更新；渲染器 TOC 边框用）
        self.width: int = 80
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
        # 反向历史搜索状态（None=未激活；input_area 渲染覆盖行）
        self.history_search: "HistorySearchState | None" = None

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
        """将块内 AnsiLine（从 start 起）转为 ink Line（推理块叠加 dim/italic）。

        方向D 步骤15：工具块标题行前置状态图标（running ● / done ✔ / fail ✖，
        渲染装饰不改动 block.lines 原文；仅 start==0 时前置一次）。
        """
        from src.tui.ink import Line, StyledRun
        from src.renderer.ansi.style import Style as _AnsiStyle
        slice_lines = block.lines[start:]
        if not slice_lines:
            return []
        reasoning_style = (
            _AnsiStyle(dim=True, italic=True) if block.kind == "reasoning" else None
        )
        icon_runs = (
            _tool_icon_runs(block) if (block.kind == "tool" and start == 0) else []
        )
        out: list = []
        for idx, ansi_line in enumerate(slice_lines):
            runs = []
            for r in ansi_line.runs:
                if not r.text:
                    continue
                st = r.style
                if reasoning_style is not None:
                    st = reasoning_style if st is None else st.merge(reasoning_style)
                runs.append(StyledRun(r.text, st))
            if idx == 0 and icon_runs:
                runs = icon_runs + runs
            out.append(Line(runs))
        return out

    # ── 推理/内容通道 ───────────────────────────────

    def ensure_reasoning(self):
        """确保推理通道开启（返回渲染器，None 表示已关闭）。"""
        if self.reasoning_state == ReasoningState.CLOSED:
            return None
        if self.reasoning_renderer is None:
            from src.renderer.ansi import AnsiStreamRenderer
            self.reasoning_renderer = AnsiStreamRenderer(width=self.width)
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
        # 提交到增量渲染缓存（方向D 步骤15：关闭块冻结行缓存）
        if 0 <= self.reasoning_block_index < len(self.blocks):
            block = self.blocks[self.reasoning_block_index]
            block.closed = True
            block._cached_ink_lines = self._block_to_ink_lines(block, 0)
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
            self.content_renderer = AnsiStreamRenderer(width=self.width)
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
        # 提交到增量渲染缓存（方向D 步骤15：关闭块冻结行缓存）
        if 0 <= self.content_block_index < len(self.blocks):
            block = self.blocks[self.content_block_index]
            block.closed = True
            block._cached_ink_lines = self._block_to_ink_lines(block, 0)
            self.commit_block(self.content_block_index)

    def reopen_content(self) -> None:
        """重新打开内容通道（多轮会话新一轮内容前调用）。"""
        self.content_closed = False

    def flush_open_channels(self) -> None:
        """停止时固化所有开放通道。"""
        try:
            self.close_reasoning()
        except Exception:
            # 非关键降级：停止时通道固化失败不阻断（记录日志）
            _logger.debug("flush_open_channels 关闭推理通道异常", exc_info=True)
        try:
            self.close_content()
        except Exception:
            _logger.debug("flush_open_channels 关闭内容通道异常", exc_info=True)

    # ── 工具 box（每工具一个，增量刷新） ────────────

    def open_tool_box(self, tool_id: str, tool_name: str, detail: str = "") -> ChatBlock:
        """打开一个工具分组：标题行立即显示，输出增量追加（无边框）。

        方向D 步骤15：extra 记录工具状态（running）与展开标记（默认展开）；
        输出行不再增量提交 committed_lines（关闭时按可见形式统一提交/冻结，
        避免折叠/截断后 committed_lines 与块状态不一致）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        from src.tools.registry import get_tool_display_name
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        block = self.append_block("tool")
        block.extra["tool_id"] = tool_id or ""
        block.extra["tool_name"] = tool_name
        block.extra["tool_status"] = "running"
        block.extra["tool_expanded"] = True
        block.extra["tool_output_count"] = 0
        title = f"  \u00b7 {display}"
        if detail:
            title = f"  \u00b7 {display} \u00b7 {detail}"
        block.lines.append(AnsiLine.of(title, Style(fg=23, bold=True)))
        self._current_tool_box = block
        self.tool_boxes[tool_id or self._next_tool_id()] = block
        return block

    def append_tool_output(self, tool_id: str, text: str) -> None:
        """追加工具输出行到对应分组（无边框）。

        方向D 步骤15：维护输出行计数（tool_output_count，供折叠/截断判定）；
        输出行不增量提交 committed_lines（关闭时统一按可见形式提交/冻结）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        block = self.tool_boxes.get(tool_id) or self._current_tool_box
        if block is None:
            block = self.open_tool_box(tool_id, "")
        for seg in text.split("\n"):
            l = AnsiLine.of("  ", Style(fg=242))
            l.append(seg)
            block.lines.append(l)
            if seg.strip():
                block.extra["tool_output_count"] = (
                    block.extra.get("tool_output_count", 0) + 1
                )

    def close_tool_box(self, tool_id: str, success: bool) -> None:
        """关闭工具分组：置状态/折叠标记、按可见形式重写行并提交（无边框）。

        方向D 步骤15：
          - extra.tool_status = done/fail（渲染层标题前置 ✔/✖ 图标）；
          - 输出行数 > tool_auto_collapse_threshold → tool_expanded=False，
            块行重写为 [标题, 折叠提示]（隐藏输出）；
          - 输出行数 > tool_output_max_lines → 截断存储行为
            [首 head_n + 省略 + 尾 tail_n + 状态]（保留首尾算法）；
          - 关闭块冻结 _cached_ink_lines（含状态图标，免每帧 Style merge）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        block = self.tool_boxes.pop(tool_id, None) or self._current_tool_box
        if block is None:
            return
        status = "\u2714" if success else "\u2716"
        block.lines.append(AnsiLine.of(f"  {status}", Style(fg=41 if success else 196)))
        block.extra["tool_status"] = "done" if success else "fail"
        output_count = block.extra.get("tool_output_count", 0)

        cfg = self._config if self._config is not None else _default_config()
        max_lines = cfg.tool_output_max_lines
        # P3-8：截断判定改用**实际存储行数**（``len(block.lines)``，含空段）——
        # append_tool_output 按 ``text.split("\\n")`` 全段追加但仅统计非空段
        # （tool_output_count）；病理输出（大量空段）时实际行数可远超
        # ``tool_output_max_lines``，旧的非空行计数判定会漏截断。
        if len(block.lines) > max_lines + 2 and len(block.lines) > 2:
            # 超长截断：保留首尾 + 省略行（head_n + 1 + tail_n = max_lines）
            head_n = max_lines // 2
            tail_n = max_lines - head_n - 1
            output = block.lines[1:-1]
            if len(output) > head_n + tail_n:
                head = output[:head_n]
                tail = output[-tail_n:] if tail_n > 0 else []
                ellipsis = AnsiLine.of(
                    f"  \u2026 \u5df2\u622a\u65ad\uff08{output_count} \u884c\u8f93\u51fa\uff09\u2026",
                    Style(fg=242),
                )
                block.lines = (
                    [block.lines[0]] + head + [ellipsis] + tail + [block.lines[-1]]
                )
                block.extra["tool_output_truncated"] = True

        if output_count > cfg.tool_auto_collapse_threshold:
            # 自动折叠：仅标题 + 折叠提示行（输出隐藏）
            block.extra["tool_expanded"] = False
            # P3-7：折叠分支同时清除截断标志——折叠后块行已重写为 [标题, 提示]，
            # 截断 extra 信息与内容不一致（修复前残留 True）。
            block.extra["tool_output_truncated"] = False
            hint = AnsiLine.of(
                f"  \u2026 \u5df2\u6298\u53e0\uff08{output_count} \u884c\u8f93\u51fa\uff09",
                Style(fg=242),
            )
            block.lines = [block.lines[0], hint]

        block.closed = True
        # 冻结行缓存（方向D 步骤15：关闭块免每帧重渲染；含标题状态图标）
        block._cached_ink_lines = self._block_to_ink_lines(block, 0)
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
    "HistorySearchState",
    "ReasoningState",
]
