"""ink 工具函数 — ANSI 剥离 / 宽度测量 / 换行截断。

所有宽度计算统一走 ``_screen.wcswidth_simple``（唯一宽度依据）。
ANSI 转义序列不占显示宽度，测量前需先剥离或识别。
"""

from __future__ import annotations

import re

from src.tui._screen import wcswidth_simple
from src.tui.core.style import Style
from .output import StyledRun, Line

# ANSI 转义序列（SGR 颜色/属性 + 光标控制）
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
)

# 光标控制序列（CUP 绝对定位 + DECRC/SCRC 光标恢复）——统一供
# ``_stdout_tracker`` 数据流顺序解析底部栏过滤（row/col 命名组）。
# 与 ``_ANSI_RE``（全量剥离）分工：本正则保留 row/col 分组语义，仅服务
# 光标控制序列解析（非纯剥离）。方向1 步骤2：三套 ANSI 正则收敛——
# ``_CONTROL_SEQ_RE`` 语义迁移至此（组名/匹配范围不变）。
cursor_control_re = re.compile(
    r"\x1b\[(?P<row>\d+);(?P<col>\d+)H"  # CUP
    r"|\x1b8"                              # DECRC
    r"|\x1b\[u"                            # SCRC
)


def strip_ansi(text: str) -> str:
    """剥离 ANSI 转义序列，返回纯文本。"""
    return _ANSI_RE.sub("", text)


def has_ansi(text: str) -> bool:
    """是否包含 ANSI 转义序列。"""
    return "\x1b" in text


def visual_width(text: str) -> int:
    """字符串显示宽度（先剥离 ANSI，再按 wcswidth_simple 测量）。"""
    return wcswidth_simple(strip_ansi(text))


def wrap_runs_by_width(runs: list[StyledRun], max_width: int) -> list[Line]:
    """将 StyledRun 序列按显示宽度换行为多行。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 每行最大显示宽度；<=0 表示不换行。

    Returns:
        换行后的 Line 列表。
    """
    if max_width <= 0:
        return [Line(runs)] if runs else []
    lines: list[Line] = []
    current = Line()
    current_width = 0
    for run in runs:
        if not run.text:
            continue
        # 单个 run 内按字符拆（保持样式一致性，逐字符累积宽度；
        # 字符先累积到 list、段级一次性 join——避免 str 不可变逐字符
        # 拼接 O(n²)；段长受换行宽度约束有界，join 成本可接受）
        text = run.text
        buf_chars: list[str] = []
        buf_width = 0
        for ch in text:
            cw = wcswidth_simple(ch)
            if current_width + buf_width + cw > max_width and (current.runs or buf_chars):
                if buf_chars:
                    current.append("".join(buf_chars), run.style)
                    buf_chars = []
                    buf_width = 0
                lines.append(current)
                current = Line()
                current_width = 0
            buf_chars.append(ch)
            buf_width += cw
        if buf_chars:
            current.append("".join(buf_chars), run.style)
            current_width += buf_width
    if current.runs:
        lines.append(current)
    return lines


def truncate_runs(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """将 StyledRun 序列截断至 max_width 显示宽度（保持样式）。

    超宽部分丢弃；截断点在字符边界，不拆分宽字符（CJK）。
    """
    if max_width < 0:
        return []
    out: list[StyledRun] = []
    width = 0
    for run in runs:
        if width >= max_width:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
    return out


def truncate_runs_ellipsis(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """将 StyledRun 序列截断至 max_width 显示宽度并追加省略号 ``…``（保持样式）。

    内容不超过 max_width 时原样返回（不追加省略号）；超过时截断内容至
    max_width-1 宽度（不拆分宽字符 CJK，宽度依据 ``wcswidth_simple``）并
    追加 ``…``（宽度 1）。省略号沿用截断点所在 run 的样式（与截断内容
    同 run，保持样式一致性）。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 最大显示宽度；<=0 返回空列表。

    Returns:
        截断后的 StyledRun 列表（总宽度 <= max_width）。
    """
    if max_width < 0:
        return []
    total = 0
    for run in runs:
        total += run.width
    if total <= max_width:
        return list(runs)
    budget = max_width - 1
    out: list[StyledRun] = []
    ellipsis_style: Style | None = runs[0].style if runs else None
    width = 0
    for run in runs:
        if width >= budget:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > budget:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
            ellipsis_style = run.style
    if width < max_width:
        out.append(StyledRun("…", ellipsis_style))
    return out


def _runs_total_width(runs: list[StyledRun]) -> int:
    """StyledRun 序列总显示宽度。"""
    total = 0
    for run in runs:
        total += run.width
    return total


def _keep_head(runs: list[StyledRun], budget: int) -> list[StyledRun]:
    """保留 runs 开头最多 budget 宽度（保持 run 顺序与样式，不拆 CJK）。"""
    out: list[StyledRun] = []
    width = 0
    for run in runs:
        if width >= budget:
            break
        buf = ""
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > budget:
                break
            buf += ch
            width += cw
        if buf:
            out.append(StyledRun(buf, run.style))
    return out


def _keep_tail(runs: list[StyledRun], budget: int) -> list[StyledRun]:
    """保留 runs 结尾最多 budget 宽度（结果保持原 run 顺序，不拆 CJK）。

    源 runs 已保证相邻同样式合并（Line.append），故保留尾部即为原序列的
    一个后缀——无需再次合并相邻同样式。
    """
    kept: list[StyledRun] = []  # 反向收集
    width = 0
    for run in reversed(runs):
        if width >= budget:
            break
        buf = ""
        for ch in reversed(run.text):
            cw = wcswidth_simple(ch)
            if width + cw > budget:
                break
            buf = ch + buf  # 字符内保持原序
            width += cw
        if buf:
            kept.append(StyledRun(buf, run.style))
    kept.reverse()
    return kept


def truncate_runs_start(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """truncate-start：省略号在开头，保留尾部内容（react-ink 语义）。

    内容不超过 max_width 时原样返回（不追加省略号）；超过时保留尾部
    ``max_width-1`` 宽度内容（不拆 CJK），开头追加 ``…``（宽度 1）。
    省略号采用尾部首个保留 run 的样式（与内容衔接一致）。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 最大显示宽度；<=0 返回空列表。

    Returns:
        截断后的 StyledRun 列表（总宽度 <= max_width）。
    """
    if max_width < 0:
        return []
    if _runs_total_width(runs) <= max_width:
        return list(runs)
    tail = _keep_tail(runs, max_width - 1)
    ellipsis_style = tail[0].style if tail else (runs[-1].style if runs else None)
    return [StyledRun("…", ellipsis_style)] + tail


def truncate_runs_middle(runs: list[StyledRun], max_width: int) -> list[StyledRun]:
    """truncate-middle：保留头尾，中间省略号（react-ink 语义）。

    内容不超过 max_width 时原样返回；超过时保留头部 ``(max_width-1)//2``
    宽度与尾部 ``max_width-1-(max_width-1)//2`` 宽度（不拆 CJK），中间
    追加 ``…``（宽度 1）。宽度 <=3 时头部预算不足（省略号+头尾各至少 1 格）
    → 回退 ``truncate-end`` 语义（末尾省略号）。

    Args:
        runs: StyledRun 列表（连续片段）。
        max_width: 最大显示宽度；<=0 返回空列表。

    Returns:
        截断后的 StyledRun 列表（总宽度 <= max_width）。
    """
    if max_width < 0:
        return []
    if _runs_total_width(runs) <= max_width:
        return list(runs)
    if max_width <= 3:
        return truncate_runs_ellipsis(runs, max_width)
    head_budget = (max_width - 1) // 2
    tail_budget = max_width - 1 - head_budget
    head = _keep_head(runs, head_budget)
    tail = _keep_tail(runs, tail_budget)
    ellipsis_style = head[-1].style if head else (tail[0].style if tail else None)
    return head + [StyledRun("…", ellipsis_style)] + tail


def build_border_box(
    title_runs: list[StyledRun],
    body_lines: list[Line] | None = None,
    width: int = 80,
    status: str = "open",
    border_style: Style | None = None,
) -> list[Line]:
    """构建边框块行列表（open/closed 两种模式，Claude TUI parity 步骤 1.3）。

    供工具盒/权限确认块复用（model 块为 AnsiLine 列表，非 ink BOX 容器——
    容器级边框 _paint_border 无法用于行级块，故独立构建行级边框）。

    - open：``┌─ title ─…─┐`` + ``│ body`` 行（不画底边，便于输出追加）。
    - closed（status != "open"）：在 open 基础上追加 ``└─ {status} ─…─┘``。

    宽度按 ``wcswidth_simple`` 截断 title/body（复用 ``truncate_runs``，
    不拆 CJK）；边框字符 ``┌─┐│└┘``，样式为 ``border_style``（默认暗青 23）。

    Args:
        title_runs: 标题 StyledRun 列表（如 ``[("⚡ " ...), ("工具名" ...)]``）。
        body_lines: 主体行列表（每行前缀 ``│ ``）；None 表示无主体。
        width: 边框块总宽度。
        status: ``"open"`` 不画底边；其他值作为底边状态文本（如 ``"✔ 完成"``）。
        border_style: 边框字符样式；None 默认 ``Style(fg=23)``。

    Returns:
        边框块 Line 列表（open=标题+主体；closed=标题+主体+底边）。
    """
    if border_style is None:
        border_style = Style(fg=23)
    lines: list[Line] = []

    # ── 顶行：┌─ title ─……─┐ ──
    head = Line.of("┌─ ", border_style)
    title_budget = max(1, width - 4)
    for run in truncate_runs(title_runs, title_budget):
        head.append_run(run)
    fill = max(0, width - 1 - head.width)
    if fill > 0:
        head.append("─" * fill, border_style)
    head.append("┐", border_style)
    lines.append(head)

    # ── 主体行：│ body（每行前缀左竖线，内容按宽截断） ──
    body_budget = max(1, width - 2)
    for bl in body_lines or []:
        body_line = Line.of("│ ", border_style)
        for run in truncate_runs(bl.runs, body_budget):
            body_line.append_run(run)
        lines.append(body_line)

    # ── 底行：└─ {status} ─……─┘（closed 模式） ──
    if status != "open":
        tail = Line.of("└─ ", border_style)
        tail.append(status, border_style)
        tail_fill = max(0, width - 1 - tail.width)
        if tail_fill > 0:
            tail.append("─" * tail_fill, border_style)
        tail.append("┘", border_style)
        lines.append(tail)

    return lines


def truncate_line(line: Line, max_width: int) -> Line:
    """将行截断至 max_width 显示宽度（保持样式）。

    超宽部分丢弃；宽度不足时原样返回。截断点在字符边界，
    不拆分宽字符（CJK）。
    """
    if max_width < 0:
        return Line()
    if line.width <= max_width:
        return line.clone()
    out = Line()
    width = 0
    for run in line.runs:
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                return out
            out.append(ch, run.style)
            width += cw
    return out


def pad_line(line: Line, width: int) -> Line:
    """将行填充至指定宽度（不足补空格；已超宽则截断）。"""
    out = truncate_line(line, width)
    pad = width - out.width
    if pad > 0:
        out.append(" " * pad)
    return out


def line_to_ansi(line: Line) -> str:
    """Line → ANSI 字符串（含行末样式重置）。"""
    return line.render()


__all__ = [
    "strip_ansi",
    "has_ansi",
    "visual_width",
    "wrap_runs_by_width",
    "truncate_runs",
    "truncate_runs_ellipsis",
    "truncate_runs_start",
    "truncate_runs_middle",
    "truncate_line",
    "pad_line",
    "line_to_ansi",
    "build_border_box",
    "cursor_control_re",
]
