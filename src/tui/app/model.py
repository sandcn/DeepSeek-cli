"""AppModel — 聊天 UI 应用模型。

单一真源：RenderCmd → AppModel 状态变更（apply.py），
组件树读取 AppModel 渲染。替代 ChatRenderState + _BottomBar 状态域。

块列表：每个聊天块 = ChatBlock（kind + AnsiLine 行列表）。
推理/内容通道：AnsiStreamRenderer 流式累积，PhaseDone 关闭后固化到块。
阶段状态机：推理 INACTIVE/ACTIVE/CLOSED + content 关闭标志（多轮重开）。

卡片结构：``committed_lines`` 为「卡片文档」——每块提交为
``[角色头] + [正文] + [空行]``（无头 kind 为 ``[正文] + [空行]``，如
user/write_line/splash/parse_info；工具块为渲染期边框卡
``[顶边框] + [主体行] + [底边框]``，顶边框替代 `▎⚡` 角色头）。角色头经
``_role_header_line`` 截断保证单行 ≤width；空行经 ``_append_card_trailer``
在块关闭提交时追加恰好一次。正文-only 冻结缓存 ``_cached_ink_lines``
不含卡片头/空行（``len == len(block.lines)`` 不变式；工具卡含底边框，
``len == len(block.lines) + 1``）。

终端 resize：``reflow_committed(width)`` 在宽度变化后按新宽度重建全部
已提交行（committed_lines 提交时按旧宽度 wrap，宽度变化后需重排）——
产出新列表对象使前缀缓存自动失效；live/开放块经 ink 布局按当前宽度换行
自动适配。
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

#: bash/execute_command 工具输出尾显示行数——超过该行数时只保留最后 N 行
#: （对齐终端 ``tail`` 语义；bash 输出常为冗长命令回显/构建日志，防卡片撑爆）。
_BASH_OUTPUT_TAIL_LINES = 3


def _single_line_detail(detail: str) -> str:
    """工具卡 detail 强制单行：换行/回车转义为字面量（``\n``/``\r``）。

    对齐 ``_subagent_render._single_line`` 单行契约——bash 命令参数可能含
    多行（``command`` 值携带 ``\n``），直接放进单行边框（卡片顶边框/标题行）
    会被终端按物理换行拆成多行，撑破工具卡边框显示错乱；显示前转义为可见
    字面量 ``\\n``/``\\r``。
    """
    if not detail:
        return ""
    return detail.replace("\r", "\\r").replace("\n", "\\n")


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
    # 分栏说明模式（user_select 使用）：True 时弹窗左侧选项列表、
    # 右侧显示当前选中项说明；False（命令补全等）保持描述右侧灰显的既有行为
    split_desc: bool = False


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


def _tool_card_styled_lines(block, width, start=0, stop=None):
    """工具卡片渲染期边框行（顶/底边框 + `│ ` 主体行）。

    渲染期变换：不改动 ``block.lines`` 原文（model 测试不变式
    ``block.lines[0].plain.startswith("  · ")`` / ``strip()=="✔"`` / 无边框
    字符依赖此）。顶边框仅 ``start==0``（块首次提交）；底边框仅块关闭且为
    最终块（``stop is None`` 或 ``stop >= len(block.lines)``）。关闭状态行
    ``  ✔``（模型层保留）**不渲染为主体行**——状态移入底边框
    （``└─ ✔ 完成 ─┘`` / ``└─ ✖ 失败 ─┘``，对齐 Claude Code）。

    Args:
        block: 工具块（ChatBlock.kind == "tool"）。
        width: 卡片总宽度（终端列宽）；<=0 时按无边框主体行防御渲染。
        start: 起始 AnsiLine 下标（块内行）。
        stop: 结束下标（不含）；None 表示到块末尾。

    Returns:
        list[list[StyledRun]] — 每行 StyledRun 列表（卡片边框行）。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.core.style import Style
    from src.tui.ink import StyledRun
    from src.tui.ink.helpers import truncate_runs
    from src.tools.registry import get_tool_display_name
    from src.tui._tool_icons import TOOL_ICONS
    from src.renderer.ansi.helpers import wrap_line
    pal = get_active_palette()
    border = pal.border
    width = width if isinstance(width, int) and width > 0 else 0
    inner_w = max(1, width - 4) if width > 0 else 0
    status_idx = _tool_status_index(block)
    out: list[list[StyledRun]] = []
    # 顶边框（仅 start==0）：┌─ ● ⚡ rf[ · detail] ─…─┐
    if start == 0:
        tool_name = block.extra.get("tool_name") or "工具"
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        icon_char = TOOL_ICONS.get(tool_name, "\u2699")
        detail = block.extra.get("tool_detail", "")
        # 标题 runs：状态图标（●/✔/✖）+ 工具图标 + 显示名（+ detail）。
        # 状态图标恒为 title_runs[0] → 顶边框 runs[1]（前缀 `┌─ ` 后），
        # 供 close_tool_box 原位翻转图标。
        title_runs = list(_tool_icon_runs(block))
        title_runs.append(StyledRun(f"{icon_char} ", Style(fg=252)))
        title_runs.append(StyledRun(display, Style(fg=252)))
        if detail:
            title_runs.append(StyledRun(f" \u00b7 {detail}", pal.dim))
        head = [StyledRun("\u250c\u2500 ", border)]
        if width > 0:
            for run in truncate_runs(title_runs, max(1, width - 4)):
                head.append(run)
            fill = max(0, width - 1 - sum(r.width for r in head))
            if fill > 0:
                head.append(StyledRun("\u2500" * fill, border))
            head.append(StyledRun("\u2510", border))
        else:
            head.extend(title_runs)
        out.append(head)
    # 主体行：block.lines[start:stop]，start==0 时跳过标题行（名字已在顶边框）；
    # 关闭状态行（_tool_status_index）跳过——状态已移入底边框（对齐 Claude Code）
    body_end = len(block.lines) if stop is None else min(stop, len(block.lines))
    body_start = start if start > 0 else 1
    # bash 尾显示：前置省略提示行「… 前 N 行省略」（仅首次提交 start==0）
    omitted = block.extra.get("_bash_omitted_lines", 0)
    if omitted > 0:
        ind_runs = [StyledRun(f"\u2026 前 {omitted} 行省略", Style(fg=242))]
        if width > 0:
            # 窄屏防溢出：省略提示截断至内宽（与顶/底边框一致——不截断时提示
            # 文本（如「… 前 5000 行省略」）超内宽会撑破卡片边框，窄终端错乱）
            ind_runs = truncate_runs(ind_runs, inner_w)
            body = [StyledRun("\u2502 ", border)] + ind_runs
            pad = inner_w - sum(r.width for r in ind_runs)
            if pad > 0:
                body.append(StyledRun(" " * pad, border))
            body.append(StyledRun(" \u2502", border))
            out.append(body)
        else:
            out.append(ind_runs)
    for abs_idx in range(body_start, body_end):
        if status_idx is not None and abs_idx == status_idx:
            continue
        ansi_line = block.lines[abs_idx]
        wrapped = (
            wrap_line(ansi_line, inner_w)
            if width > 0
            else ([ansi_line] if ansi_line.runs else [])
        )
        if not wrapped:
            # 空输出行 → 有边框空行 `│    │`（保持行映射）
            if width > 0:
                out.append([StyledRun("\u2502 " + " " * inner_w + " \u2502", border)])
            continue
        for seg in wrapped:
            seg_runs = [StyledRun(r.text, r.style) for r in seg.runs if r.text]
            if width <= 0:
                out.append(seg_runs)
                continue
            body = [StyledRun("\u2502 ", border)] + seg_runs
            pad = inner_w - sum(r.width for r in seg_runs)
            if pad > 0:
                body.append(StyledRun(" " * pad, border))
            body.append(StyledRun(" \u2502", border))
            out.append(body)
    # 底边框：仅关闭块且为最终块；含状态文本（对齐 Claude Code `└─ ✔ 完成 ─┘`）
    if block.closed and (stop is None or stop >= len(block.lines)):
        if width > 0:
            tail = [StyledRun("\u2514\u2500 ", border)]
            status = block.extra.get("tool_status", "running")
            if status == "done":
                status_runs = [StyledRun("\u2714 完成", Style(fg=41))]
            elif status == "fail":
                status_runs = [StyledRun("\u2716 失败", Style(fg=196))]
            else:
                status_runs = []
            # 窄屏防溢出：状态文本截断至剩余宽度（└─/┘ 占用 4 列 → 预算 width-4）
            for run in truncate_runs(status_runs, max(1, width - 4)):
                tail.append(run)
            fill = max(0, width - 1 - sum(r.width for r in tail))
            if fill > 0:
                tail.append(StyledRun("\u2500" * fill, border))
            tail.append(StyledRun("\u2518", border))
            out.append(tail)
        else:
            out.append([StyledRun("\u2514\u2518", border)])
    return out


def _tool_status_index(block):
    """工具卡状态行下标（close 追加的 `  ✔`/`  ✖` 行）；无则返回 None。

    关闭工具块时 ``close_tool_box`` 追加状态行到 block.lines 末尾（模型层
    不变式 ``block.lines[-1].plain.strip()=="✔"``）。卡片渲染把状态移到底边框
    （``└─ ✔ 完成 ─┘``，对齐 Claude Code），渲染主体行时跳过该行。
    ``_status_line_index`` 由 close_tool_box 记录（歧义安全）；回退按末行
    plain 匹配（覆盖 reflow/旧块等未记录场景）。
    """
    idx = block.extra.get("_status_line_index")
    if idx is not None:
        return idx
    if block.closed and block.lines:
        last = block.lines[-1]
        if getattr(last, "plain", "").strip() in ("\u2714", "\u2716"):
            return len(block.lines) - 1
    return None


def _user_marker_styled_lines(block, start, stop, width):
    """用户消息每行 `> ` 标记（渲染期变换，每行 `> {segment}` 顶格）。

    输入为 ``build_user_line`` 产出的 AnsiLine（runs[0]=`> ` 前缀 run）：
    剥离前缀 run → 内容 wrap 到 ``inner=width-2`` → 重前缀（每段均 ``> ``，
    Claude Code 顶格列 0，续行同样带 ``> ``——≤ width 安全，满足行级 diff
    宽度不变量）。

    Args:
        block: 用户块（ChatBlock.kind == "user"）。
        start: 起始 AnsiLine 下标。
        stop: 结束下标（不含）；None 表示到块末尾。
        width: 文档宽度；<=0 时仅剥离前缀不换行。

    Returns:
        list[Line] — 用户消息 ink Line 列表。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.ink import Line, StyledRun
    from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line
    pal = get_active_palette()
    icon = pal.user_icon
    width = width if isinstance(width, int) and width > 0 else 0
    inner = max(1, width - 2) if width > 0 else 0
    out: list[Line] = []
    for ansi_line in block.lines[start:stop]:
        runs = list(ansi_line.runs)
        # 剥离 `> ` 前缀 run（build_user_line 结构：runs[0] = 图标前缀）
        if runs and runs[0].text.startswith("> "):
            first = runs[0]
            if len(first.text) > 2:
                content_runs = [Run(first.text[2:], first.style)] + runs[1:]
            else:
                content_runs = runs[1:]
        else:
            content_runs = runs
        wrapped = (
            wrap_line(AnsiLine(content_runs), inner)
            if (width > 0 and content_runs)
            else ([AnsiLine(content_runs)] if content_runs else [])
        )
        if not wrapped:
            # 空消息 → 保留前缀行 `> `（不吞行）
            out.append(Line([StyledRun("> ", icon)]))
            continue
        for seg in wrapped:
            seg_runs = [StyledRun(r.text, r.style) for r in seg.runs if r.text]
            out.append(Line([StyledRun("> ", icon)] + seg_runs))
    return out


def _role_header_runs(block, model) -> list:
    """构建块角色头 StyledRun 列表（卡片首行，按 kind 选样式与文本）。

    无头 kind（content/tool/user/write_line/splash/parse_info）返回空列表
    （不占行）——content 对齐 Claude Code 无头回答；tool 由卡片顶边框替代。
    样式取活动调色板槽位（``get_active_palette()``，dark 下与既有常量同值）；
    reasoning/error 用硬编码兜底（与正文样式语义一致）。
    """
    from src.tui.app._theme import get_active_palette
    from src.tui.core.style import Style
    from src.tui.ink import StyledRun
    kind = block.kind
    pal = get_active_palette()
    if kind == "content":
        # 助手回答无角色头（对齐 Claude Code：markdown 文本直接流动，无
        # `▎回答` 头行；用户消息以 `> ` 前缀区分）
        return []
    if kind == "reasoning":
        return [StyledRun("\u258d\U0001f4ad 思考", Style(fg=242, italic=True))]
    if kind == "tool":
        # 工具卡片顶边框替代 `▎⚡ 工具 X` 角色头（卡片化对齐 Claude Code）；
        # 无头 → _card_lines 不前置独立头行，顶边框即卡片首行。
        return []
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

    def _block_to_ink_lines(self, block, start: int = 0, stop=None):
        """将块内 AnsiLine（从 start 起到 stop）转为 ink Line（块级样式叠加）。

        ★ 方向1 P0-1（超宽行 wrap）：committed 发射前按 ``self.width`` wrap——
        任一 AnsiLine 显示宽度超过终端宽度时，经 ``renderer.ansi.helpers.wrap_line``
        拆为多行（保持 run 样式），避免超宽行破坏行级 diff 模型（committed_lines
        每行 ink Line 宽度须 <= width）。仅超宽行走 wrap（普通行零额外成本）；
        ``self.width <= 0`` 时跳过 wrap 保持原样（防御）。

        分支（先于通用 wrap）：
          - tool：卡片化边框行（顶/底边框 + `│ ` 主体行）——渲染期变换，不改动
            ``block.lines`` 原文；builder 统一管理宽度约束，不走通用 wrap。
          - user：每行 ``> `` 标记（顶格列 0，对齐 Claude Code）——即使不超宽
            也要重前缀。

        stop: 可选结束下标（不含）——``reflow_committed`` 重建块**已提交部分**
        （``lines[:committed_line_count]``）时限定范围，避免未提交尾混入。
        """
        from src.tui.ink import Line, StyledRun
        from src.renderer.ansi.style import Style as _AnsiStyle
        from src.renderer.ansi.helpers import wrap_line
        slice_lines = block.lines[start:stop]
        if not slice_lines:
            return []
        # 工具卡片（渲染期边框行）：builder 统一管理宽度约束；不走通用 wrap
        if block.kind == "tool":
            return [
                Line(runs) for runs in _tool_card_styled_lines(
                    block, getattr(self, "width", 0), start, stop,
                )
            ]
        # 用户消息（每行 `> ` 标记）：即使不超宽也要重前缀 → 先于通用 wrap
        if block.kind == "user":
            return _user_marker_styled_lines(
                block, start, stop, getattr(self, "width", 0),
            )
        reasoning_style = (
            _AnsiStyle(dim=True, italic=True) if block.kind == "reasoning" else None
        )
        width = getattr(self, "width", 0)
        out: list = []
        for ansi_line in slice_lines:
            # ★ 方向1 P0-1：超宽行按 width wrap（wrap 与测量使用一致的宽度工具；
            #   仅超宽行走 wrap，普通行零额外成本；width<=0 跳过 wrap 防御）
            src_lines = (
                wrap_line(ansi_line, width)
                if (width > 0 and ansi_line.width > width)
                else [ansi_line]
            )
            for wrapped in src_lines:
                runs = []
                for r in wrapped.runs:
                    if not r.text:
                        continue
                    st = r.style
                    if reasoning_style is not None:
                        st = reasoning_style if st is None else st.merge(reasoning_style)
                    runs.append(StyledRun(r.text, st))
                out.append(Line(runs))
        return out

    def _card_lines(self, block, start: int = 0):
        """块卡片行：正文 + （首次提交时）角色头。

        committed_lines 为「卡片文档」（角色头 + 正文 + 空行；content/tool 无
        角色头——content 对齐 Claude Code 无头回答，tool 由卡片顶边框替代）。
        角色头仅在 start==0（块首次提交，committed_line_count==0）时前置一次；
        增量提交（start>0）不再重复。冻结行 ``_cached_ink_lines`` 保持正文-only
        （不改，测试锁定 ``len(_cached_ink_lines) == len(block.lines)``）。
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

    def _card_lines_committed(self, block, width: int) -> list:
        """重建块**已提交部分**（``block.lines[:committed_line_count]``）的卡片行。

        供 ``reflow_committed``（终端宽度变化重排）使用：按新宽度重新 wrap
        已提交行 + 角色头（首行）+ 关闭块（已完全提交）尾空行——与提交路径
        （``_card_lines`` + ``_append_card_trailer``）产出一致，保证头/空行
        重建后恰好一次。``stop`` 限定只取已提交 AnsiLine（open 块未提交尾
        不混入）。
        """
        count = block.committed_line_count
        if count <= 0:
            return []
        out = self._block_to_ink_lines(block, 0, stop=count)
        header = _role_header_line(block, self, width)
        if header is not None:
            out = [header] + out
        if block.closed and count >= len(block.lines) and block.lines:
            if getattr(block.lines[-1], "plain", "") != "":
                from src.tui.ink import Line
                out.append(Line())
        return out

    def reflow_committed(self, width: int) -> None:
        """终端宽度变化后按新宽度重建 committed_lines（重排已提交行）。

        committed_lines 在提交时按旧宽度 wrap；宽度变化后旧行可能超宽（破坏
        「行级 diff 宽度不变量」：committed 每行 ink Line 宽度须 <= width）或
        未利用新宽度。重建全部已提交行：逐块按已提交行数重新 wrap + 卡片头 +
        关闭块尾空行，产出**新列表对象** → ChatView use_memo / committed-chat
        前缀缓存（键含 ``id(lines)``）自动失效，无需额外通知。open 块未提交
        尾留在块内（live 渲染按新宽度 wrap）；关闭块未提交尾
        （``_cached_ink_lines``）同步按新宽度重冻结。

        幂等：``width <= 0`` 或与当前宽度相同 → 直接返回（零成本）。
        """
        if width <= 0 or width == self.width:
            return
        self.width = width
        committed: list = []
        for block in self.blocks:
            count = block.committed_line_count
            if count <= 0:
                continue
            block.extra["_first_committed_offset"] = len(committed)
            committed.extend(self._card_lines_committed(block, width))
            # 关闭块未提交尾（增量提交后仍留尾 / 被夹住）：按新宽度重冻结
            if block._cached_ink_lines is not None and count < len(block.lines):
                block._cached_ink_lines = self._block_to_ink_lines(block, count)
        self.committed_lines = committed

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
        """关闭推理通道：固化渲染器输出（无分隔线——对齐 Claude Code 消息间仅空行）。"""
        if self.reasoning_state == ReasoningState.CLOSED:
            return
        rr = self.reasoning_renderer
        if rr is not None:
            rr.close()
            lines = rr.take_lines()
            if 0 <= self.reasoning_block_index < len(self.blocks):
                block = self.blocks[self.reasoning_block_index]
                block.lines.extend(lines)
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
        """打开一个工具分组：卡片顶边框立即显示，输出增量追加（卡片化）。

        方向D 步骤15：extra 记录工具状态（running）与顶边框 detail
        （``tool_detail``）；输出行不再增量提交 committed_lines（关闭时统一
        提交/冻结，避免 committed_lines 与块状态不一致）。
        """
        from src.tui.core.style import Style
        from src.renderer.ansi.helpers import AnsiLine
        from src.tools.registry import get_tool_display_name
        display = get_tool_display_name(tool_name) or tool_name or "工具"
        block = self.append_block("tool")
        block.extra["tool_id"] = tool_id or ""
        block.extra["tool_name"] = tool_name
        block.extra["tool_status"] = "running"
        # 工具卡片顶边框 detail 数据源（_tool_card_styled_lines 消费）；
        # ★ bash 多行命令 detail 含 \n——强制单行转义（对齐 _single_line 契约，
        #   防 \n 拆破单行边框）。title/active_tool 复用转义后值（同源单行）。
        detail = _single_line_detail(detail)
        block.extra["tool_detail"] = detail
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
        """追加工具输出行到对应分组（卡片主体行）。

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
        # bash/execute_command：输出超过阈值行数时只保留最后 N 行（tail 显示，
        # 对齐 Claude Code 收敛冗长 bash 输出；修剪后行数 ≤ N+1，不触发增量提交）
        if block.extra.get("tool_name") in ("bash", "execute_command"):
            self._trim_tool_output_tail(block, _BASH_OUTPUT_TAIL_LINES)
        # ★ 方向4：增量提交阈值——长工具输出不每帧全量重渲染（超过阈值即提交
        #   已闭合行到 committed_lines；开放块渲染只取未提交尾）。
        if len(block.lines) - block.committed_line_count >= _TOOL_INCREMENTAL_THRESHOLD:
            self.commit_open_block(block)

    def _trim_tool_output_tail(self, block, keep: int) -> None:
        """工具块输出修剪为最后 keep 行（保留标题行 block.lines[0]）。

        bash 尾显示：输出超过 keep 行时删除前置输出行（下标 1..N-keep），
        累计省略数记入 ``block.extra["_bash_omitted_lines"]``（卡片渲染时
        前置「… 前 N 行省略」提示）；同步 ``committed_line_count``（已提交行
        被删则回退计数，防越界/重复提交）。修剪后行数 ≤ 1+keep，远低于增量
        提交阈值 → 无增量提交。
        """
        lines = block.lines
        if len(lines) <= 1 + keep:
            return
        del_count = len(lines) - 1 - keep
        del lines[1:1 + del_count]
        block.extra["_bash_omitted_lines"] = (
            block.extra.get("_bash_omitted_lines", 0) + del_count
        )
        if block.committed_line_count > 0:
            block.committed_line_count = max(0, block.committed_line_count - del_count)

    def close_tool_box(self, tool_id: str, success: bool) -> None:
        """关闭工具分组：置状态、冻结并提交（工具卡片）。

        方向D 步骤15：
          - extra.tool_status = done/fail（卡片顶边框状态图标原位翻转 ✔/✖）；
          - 关闭块冻结 _cached_ink_lines（含底边框，免每帧 Style merge）。

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
        # 记录状态行下标（卡片渲染跳过该主体行——状态已移入底边框；模型层
        # 不变式 block.lines[-1].plain.strip()=="✔" 保留）
        block.extra["_status_line_index"] = len(block.lines)
        block.lines.append(AnsiLine.of(f"  {status}", Style(fg=41 if success else 196)))
        block.extra["tool_status"] = "done" if success else "fail"
        # Claude TUI parity 步骤 2.2：关闭后无进行中工具（ToolStatusHeader 隔离
        # 测试仍消费 active_tool；app 组件树已移除该组件）
        self.active_tool = None

        # ★ 1.6 修复：长工具输出（> _TOOL_INCREMENTAL_THRESHOLD 触发增量提交后
        #   顶边框已在 committed_lines）关闭时更新 committed_lines 中顶边框状态
        #   图标——修复前 committed_lines 顶边框恒 ●（首帧增量提交时状态为
        #   running，close 后不再更新）。替换 runs 时新建 StyledRun 列表但保留
        #   Line 对象引用（增量缓存身份复用不破坏）；未触发增量提交的短工具
        #   （_first_committed_offset 不存在）关闭时经 commit_block 提交的顶边框
        #   已带 done/fail 图标，无需更新。
        #   卡片结构：``_first_committed_offset`` 指向卡片**首行（顶边框）**，
        #   状态图标为边框内 runs[1]（``┌─ `` 前缀后；runs[0] 为边框前缀）。
        offset = block.extra.get("_first_committed_offset")
        if offset is not None and 0 <= offset < len(self.committed_lines):
            icon = _tool_icon_runs(block)
            if icon:
                top_line = self.committed_lines[offset]
                runs = list(top_line.runs)
                # 顶边框结构：[0]=`┌─ ` 边框前缀, [1]=状态图标, [2:]=标题内容
                idx = 1
                if not (len(runs) > 1 and runs[0].text.startswith("\u250c")):
                    # 防御：超窄宽度下标题被截断时按图标字符扫描定位
                    for i, r in enumerate(runs):
                        if r.text and r.text.strip() in ("\u25cf", "\u2714", "\u2716"):
                            idx = i
                            break
                # 替换图标 run，保留边框前缀与标题内容（runs 引用身份保留）
                top_line.runs = runs[:idx] + icon + runs[idx + 1:]

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
