"""AppModel — 聊天 UI 应用模型。

单一真源：RenderCmd → AppModel 状态变更（apply.py），
组件树读取 AppModel 渲染。替代 ChatRenderState + _BottomBar 状态域。

块列表：每个聊天块 = ChatBlock（kind + AnsiLine 行列表）。
推理/内容通道：AnsiStreamRenderer 流式累积，PhaseDone 关闭后固化到块。
阶段状态机：推理 INACTIVE/ACTIVE/CLOSED + content 关闭标志（多轮重开）。

卡片结构：``committed_lines`` 为「卡片文档」——每块提交为
``[角色头] + [正文] + [空行]``（无头 kind 为 ``[正文] + [空行]``，如
user/write_line/splash/parse_info）。角色头经 ``_role_header_line`` 截断
保证单行 ≤width；空行经 ``_append_card_trailer`` 在块关闭提交时追加恰好
一次。正文-only 冻结缓存 ``_cached_ink_lines`` 不含卡片头/空行
（``len == len(block.lines)`` 不变式）。
"""

from __future__ import annotations

import logging
import time
from src._compat import dataclass
from dataclasses import field
from enum import Enum
from typing import Any

_logger = logging.getLogger(__name__)

#: 开放工具块增量提交阈值（方向4）——输出行超出该阈值时经 commit_open_block
#: 增量提交已闭合行到 committed_lines（长工具输出每帧不再全量重渲染）。
_TOOL_INCREMENTAL_THRESHOLD = 64


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
    #: 开放块 styled 引用缓存（dict[AnsiLine, list[StyledRun]]，方向1）——
    #: ``_block_styled_lines`` 按行对象缓存 AnsiLine→StyledRun 转换结果，使
    #: ``_measure`` 的 ``cache[0] is styled`` 身份快路径跨帧命中（大 open 块
    #: 每帧零重建）。行被 block.lines 持有，dict 随 block GC 自然释放。
    _open_styled_cache: dict | None = None


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
    # 斜杠命令描述（Claude TUI parity 步骤 3.7；与 items 对齐，缺省空列表）
    descriptions: list = field(default_factory=list)


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


def _role_header_runs(block, model) -> list:
    """构建块角色头 StyledRun 列表（卡片首行，按 kind 选样式与文本）。

    无头 kind（user/write_line/splash/parse_info）返回空列表（不占行）。
    样式取活动调色板槽位（``get_active_palette()``，dark 下与既有常量同值）；
    reasoning/error 用硬编码兜底（与正文样式语义一致）。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.core.style import Style
    from src.tui.ink import StyledRun
    kind = block.kind
    pal = get_active_palette()
    if kind == "content":
        return [StyledRun("\u258e", pal.accent_bold), StyledRun("回答", pal.text)]
    if kind == "reasoning":
        return [StyledRun("\u258d\U0001f4ad 思考", Style(fg=242, italic=True))]
    if kind == "tool":
        tool_name = block.extra.get("tool_name") or "工具"
        return [StyledRun("\u258e\u26a1", pal.accent), StyledRun(f" 工具 {tool_name}", pal.dim)]
    if kind == "notification":
        return [StyledRun("\u258e", pal.notice), StyledRun("通知", pal.notice)]
    if kind == "error":
        return [StyledRun("\u258e错误", Style(fg=196, bold=True))]
    if kind == "subagent":
        return [StyledRun("\u258e", pal.dim), StyledRun("子代理", pal.dim)]
    return []


def _role_header_line(block, model, width) -> "Line | None":
    """构建块角色头行（单行，截断至 width 满足行级 diff 宽度不变量）。

    无头 kind 返回 None。头部必须单行且宽度 <= width（committed_lines 每行
    ink Line 宽度 <= width 不变量）；width<=0 时保持原样（防御）。
    """
    runs = _role_header_runs(block, model)
    if not runs:
        return None
    from src.tui.ink import Line
    from src.tui.ink.helpers import truncate_runs
    if width and width > 0:
        runs = truncate_runs(runs, width)
    return Line(runs)


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
        # 终端宽度（session 每帧更新；渲染器 TOC 边框用）
        self.width: int = 80
        # 工具调用组
        self.in_tool_group: bool = False
        self.tool_block_index: int = -1
        # 每工具 box 跟踪（tool_id → 开放 box）
        self.tool_boxes: dict = {}
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
        # 顶部工具调用状态（Claude TUI parity 步骤 2.2：active_tool 供
        # ToolStatusHeader 渲染；None=无进行中工具，不占行）
        self.active_tool: dict | None = None

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
        # ★ 1.6：块首次提交（committed_line_count==0）记录卡片首行（角色头）
        #   在 committed_lines 中的偏移（committed_lines 只增不删，偏移稳定），
        #   供 close_tool_box 关闭时更新其后一行（正文标题）状态图标。
        #   open 块不加卡片尾空行（_append_card_trailer 仅关闭提交时追加）。
        if block.committed_line_count == 0:
            block.extra.setdefault("_first_committed_offset", len(self.committed_lines))
        self.committed_lines.extend(
            self._card_lines(block, block.committed_line_count)
        )
        block.committed_line_count = len(block.lines)

    def commit_block(self, index: int) -> None:
        """提交 blocks[committed_count..index] 到增量渲染缓存。

        仅提交**连续的已关闭**块——前面若有未关闭块（如流式内容块）则停止，
        避免跳过开放块导致其后续行丢失。已增量提交的行（committed_line_count）
        不再重复渲染。卡片结构：本次有新增内容提交时经 ``_card_lines`` 发射
        （首次提交带头行）并追加卡片尾空行 ``_append_card_trailer``。

        ★ 方向5（append_committed 冻结）：全块提交完成（closed 且
        committed_line_count == len(lines)）且尚未冻结时建立 ``_cached_ink_lines``
        ——append_committed 创建的立即关闭块自动冻结（免每帧重渲染）；
        被开放块夹住的已关闭块提交后同样冻结。仅 ``is None`` 时冻结：
        close_reasoning/close_content/close_tool_box 已在关闭时冻结（内容
        可能不同——如 close_tool_box 冻结未提交尾），不覆盖。
        """
        while self.committed_count <= index and self.committed_count < len(self.blocks):
            block = self.blocks[self.committed_count]
            if not block.closed:
                break
            if block.committed_line_count < len(block.lines):
                # ★ 1.6：块首次提交（committed_line_count==0）记录卡片首行偏移
                #   （与 commit_open_block 一致——增量提交路径也须记录）。
                if block.committed_line_count == 0:
                    block.extra.setdefault("_first_committed_offset", len(self.committed_lines))
                self.committed_lines.extend(
                    self._card_lines(block, block.committed_line_count)
                )
                block.committed_line_count = len(block.lines)
                # ★ 卡片尾空行：块关闭提交（本次有新增内容）时追加一个空行
                #   分隔卡片——幂等重入（committed_line_count >= len(lines)
                #   提前返回）时不再追加，空行恰好一次。
                self._append_card_trailer(block)
            if block._cached_ink_lines is None:
                block._cached_ink_lines = self._block_to_ink_lines(block, 0)
                # ★ 方向1（内存回收）：冻结后开放 styled 缓存不再被
                #   ``_block_styled_lines`` 使用（改走冻结缓存）——释放引用防
                #   大会话累积（dict 持有全部已转换行引用）。
                block._open_styled_cache = None
            self.committed_count += 1

    def _block_to_ink_lines(self, block, start: int = 0):
        """将块内 AnsiLine（从 start 起）转为 ink Line（推理块叠加 dim/italic）。

        ★ 方向1 P0-1（超宽行 wrap）：committed 发射前按 ``self.width`` wrap——
        任一 AnsiLine 显示宽度超过终端宽度时，经 ``renderer.ansi.helpers.wrap_line``
        拆为多行（保持 run 样式），避免超宽行破坏行级 diff 模型（committed_lines
        每行 ink Line 宽度须 <= width）。仅超宽行走 wrap（普通行零额外成本）；
        ``self.width <= 0`` 时跳过 wrap 保持原样（防御）。

        方向D 步骤15：工具块标题行前置状态图标（running ● / done ✔ / fail ✖，
        渲染装饰不改动 block.lines 原文；仅 start==0 时前置一次）。
        """
        from src.tui.ink import Line, StyledRun
        from src.renderer.ansi.style import Style as _AnsiStyle
        from src.renderer.ansi.helpers import wrap_line
        slice_lines = block.lines[start:]
        if not slice_lines:
            return []
        reasoning_style = (
            _AnsiStyle(dim=True, italic=True) if block.kind == "reasoning" else None
        )
        icon_runs = (
            _tool_icon_runs(block) if (block.kind == "tool" and start == 0) else []
        )
        width = getattr(self, "width", 0)
        out: list = []
        for idx, ansi_line in enumerate(slice_lines):
            # ★ 方向1 P0-1：超宽行按 width wrap（wrap 与测量使用一致的宽度工具；
            #   仅超宽行走 wrap，普通行零额外成本；width<=0 跳过 wrap 防御）
            src_lines = (
                wrap_line(ansi_line, width)
                if (width > 0 and ansi_line.width > width)
                else [ansi_line]
            )
            first = True
            for wrapped in src_lines:
                runs = []
                for r in wrapped.runs:
                    if not r.text:
                        continue
                    st = r.style
                    if reasoning_style is not None:
                        st = reasoning_style if st is None else st.merge(reasoning_style)
                    runs.append(StyledRun(r.text, st))
                if idx == 0 and icon_runs and first:
                    runs = icon_runs + runs
                first = False
                out.append(Line(runs))
        return out

    def _card_lines(self, block, start: int = 0):
        """块卡片行：正文 + （首次提交时）角色头。

        committed_lines 为「卡片文档」（角色头 + 正文 + 空行）。角色头仅在
        start==0（块首次提交，committed_line_count==0）时前置一次；增量提交
        （start>0）不再重复。冻结行 ``_cached_ink_lines`` 保持正文-only（不改，
        测试锁定 ``len(_cached_ink_lines) == len(block.lines)``）。
        """
        out = self._block_to_ink_lines(block, start)
        if start == 0:
            header = _role_header_line(block, self, getattr(self, "width", 0))
            if header is not None:
                out = [header] + out
        return out

    def _append_card_trailer(self, block) -> None:
        """块完全提交后追加卡片尾空行（卡片与下一条目分隔）。

        仅当正文末行非空时追加（正文已以空行结尾则跳过，防双空行）。
        committed_lines 原地增长（引用不变），前缀缓存兼容。
        """
        if not block.lines:
            return
        if getattr(block.lines[-1], "plain", "") == "":
            return
        from src.tui.ink import Line
        self.committed_lines.append(Line())

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
            block._open_styled_cache = None  # 冻结后开放缓存不再需要
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
            block._open_styled_cache = None  # 冻结后开放缓存不再需要
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

        方向D 步骤15：extra 记录工具状态（running）；输出行不再增量提交
        committed_lines（关闭时统一提交/冻结，避免 committed_lines 与块状态
        不一致）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        from src.tools.registry import get_tool_display_name
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        block = self.append_block("tool")
        block.extra["tool_id"] = tool_id or ""
        block.extra["tool_name"] = tool_name
        block.extra["tool_status"] = "running"
        title = f"  \u00b7 {display}"
        if detail:
            title = f"  \u00b7 {display} \u00b7 {detail}"
        block.lines.append(AnsiLine.of(title, Style(fg=23, bold=True)))
        # 方向1 B8：记录实际存储 key（非空 tool_id 即自身；空 tool_id 场景为
        # _next_tool_id() 生成值）。``_box_key`` 记录**原始传入 tool_id**——
        # 非空时即实际存储 key（tool_boxes 按原 id 存取）；空 id 场景为 ""，
        # 供 ``close_tool_box("")`` 按空 id 匹配匿名 box 关闭（修复空 tool_id
        # box 泄漏：旧实现空 id open 存于生成 key，close("") 永远 pop 不到）。
        key = tool_id or self._next_tool_id()
        block.extra["_box_key"] = tool_id
        self.tool_boxes[key] = block
        # Claude TUI parity 步骤 2.2：记录进行中工具（ToolStatusHeader 消费）
        self.active_tool = {
            "name": display, "detail": detail, "status": "running",
            "tool_name": tool_name or "",
        }
        return block

    def append_tool_output(self, tool_id: str, text: str) -> None:
        """追加工具输出行到对应分组（无边框）。

        方向4（开放工具块增量提交）：输出行数超过阈值
        （``_TOOL_INCREMENTAL_THRESHOLD``）时经 ``commit_open_block`` 增量提交
        已闭合行到 committed_lines——长工具输出每帧不再全量重渲染（开放块只
        渲染未提交尾）；关闭时 ``commit_block`` 追加剩余尾（含状态行），
        ``committed_line_count`` 计数保证不重复（「关闭后无重复行」不变量）。

        Bug A 修复：按 tool_id 精确路由——key 命中精确追加；key 未命中且
        tool_id 非空 → 创建匿名 box（标题回退「工具」，输出不丢失）；
        tool_id 为空 → 丢弃并 debug 日志（无归属输出不静默错路由）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine, ansi_to_line
        block = self.tool_boxes.get(tool_id)
        if block is None:
            if not tool_id:
                _logger.debug(
                    "append_tool_output: 收到空 tool_id，输出丢弃: %.80s", text,
                )
                return
            block = self.open_tool_box(tool_id, "")
        for seg in text.split("\n"):
            l = AnsiLine.of("  ", Style(fg=242))
            # ★ 工具输出可能含 Rich/pygments 高亮 ANSI 序列（read_file 等）。
            #   原样保留进 Run.text 会让宽度测量把转义码当可见字符（宽度膨胀→
            #   误触发 wrap），wrap_line 逐字符截断把转义序列拦腰截断（如残留
            #   ;49;00m）渲染错乱。经 ansi_to_line 解析为带样式 Run，宽度测量
            #   与 wrap 按样式安全处理。
            for r in ansi_to_line(seg).runs:
                l.append_run(r)
            block.lines.append(l)
        # ★ 方向4：增量提交阈值——长工具输出不每帧全量重渲染（超过阈值即提交
        #   已闭合行到 committed_lines；开放块渲染只取未提交尾）。
        if len(block.lines) - block.committed_line_count >= _TOOL_INCREMENTAL_THRESHOLD:
            self.commit_open_block(block)

    def close_tool_box(self, tool_id: str, success: bool) -> None:
        """关闭工具分组：置状态、冻结并提交（无边框）。

        方向D 步骤15：
          - extra.tool_status = done/fail（渲染层标题前置 ✔/✖ 图标）；
          - 关闭块冻结 _cached_ink_lines（含状态图标，免每帧 Style merge）。

        Bug A 修复：按 tool_id 精确 pop，不再 fallback 到 _current_tool_box
        （单值指针语义已移除）；找不到对应 box 时静默丢弃（debug 日志）。

        方向1 B8：空 tool_id 关闭——``pop("")`` 未命中且 tool_id 为空时遍历
        ``tool_boxes`` 按 ``_box_key == ""``（open 记录的原始空 id 标记）查找
        匿名 box 关闭（倒序取最近者）；找不到时静默丢弃（debug 日志）。
        修复空 tool_id box 泄漏。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        block = self.tool_boxes.pop(tool_id, None)
        if block is None and not tool_id:
            for stored_key, candidate in reversed(list(self.tool_boxes.items())):
                if candidate.extra.get("_box_key") == "":
                    block = self.tool_boxes.pop(stored_key)
                    break
        if block is None:
            _logger.debug(
                "close_tool_box: 未找到 tool_id=%r 的工具 box，静默丢弃", tool_id,
            )
            return
        status = "\u2714" if success else "\u2716"
        block.lines.append(AnsiLine.of(f"  {status}", Style(fg=41 if success else 196)))
        block.extra["tool_status"] = "done" if success else "fail"
        # Claude TUI parity 步骤 2.2：关闭后无进行中工具（ToolStatusHeader 隐藏）
        self.active_tool = None

        # ★ 1.6 修复：长工具输出（> _TOOL_INCREMENTAL_THRESHOLD 触发增量提交后
        #   标题行已在 committed_lines）关闭时更新 committed_lines 中标题行状态
        #   图标——修复前 committed_lines 标题行恒 ●（首帧增量提交时状态为
        #   running，close 后不再更新）。替换 runs 时新建 StyledRun 列表但保留
        #   Line 对象引用（增量缓存身份复用不破坏）；未触发增量提交的短工具
        #   （_first_committed_offset 不存在）关闭时经 commit_block 提交的标题行
        #   已带 done/fail 图标，无需更新。
        #   卡片结构：``_first_committed_offset`` 指向卡片**首行（角色头）**；
        #   带状态图标的正文标题行在其后一行（头部保证单行——_role_header_line
        #   截断 → 正文标题恒在 offset+1）。
        offset = block.extra.get("_first_committed_offset")
        if offset is not None and 0 <= offset + 1 < len(self.committed_lines):
            icon = _tool_icon_runs(block)
            if icon:
                title_line = self.committed_lines[offset + 1]
                # 替换图标 run（runs[0]），保留标题其余 run（runs[1:]）
                title_line.runs = icon + list(title_line.runs)[1:]

        block.closed = True
        # ★ 方向4（增量提交协同）：冻结仅**未提交部分**（已提交行在
        #   committed_lines 中，避免重复存储；``_block_styled_lines`` 冻结
        #   缓存分支已调整为 ``cache[0:]``——冻结缓存即未提交部分，start 参数
        #   对冻结缓存无意义）。关闭后 ``commit_block`` 追加剩余尾（含状态行），
        #   ``committed_line_count`` 计数保证不重复追加已提交行。
        block._cached_ink_lines = self._block_to_ink_lines(block, block.committed_line_count)
        block._open_styled_cache = None  # 冻结后开放缓存不再需要
        self.commit_block(len(self.blocks) - 1)

    def _next_tool_id(self) -> str:
        self._tool_id_seq += 1
        return f"tool-{self._tool_id_seq}"

    def reset_display(self) -> None:
        """清空显示状态（Claude TUI parity 步骤 2.2，供 Ctrl+L 清屏复用）。

        清空聊天块/增量缓存/推理内容通道/subagent 行/进行中工具/解析行，
        保留 ``status/input_text/input_cursor/completion``（用户输入与状态不丢）。
        调用方须保证非流式（status.status_active=False）时调用，避免丢未提交块。
        """
        self.blocks = []
        self.committed_lines = []
        self.committed_count = 0
        self.reasoning_renderer = None
        self.content_renderer = None
        self.reasoning_state = ReasoningState.INACTIVE
        self.content_closed = False
        self.reasoning_block_index = -1
        self.content_block_index = -1
        self.in_tool_group = False
        self.tool_block_index = -1
        self.tool_boxes = {}
        self._tool_id_seq = 0
        self.parse_line = None
        self.subagent_lines = []
        self.active_tool = None


__all__ = [
    "AppModel",
    "ChatBlock",
    "CompletionState",
    "StatusState",
    "HistorySearchState",
    "ReasoningState",
]
