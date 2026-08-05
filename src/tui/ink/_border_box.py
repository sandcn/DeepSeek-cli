"""边框块构建 — build_border_box（行级边框，非容器级）。

模块边界（2026-08-05 架构优化）：从 ``ink/helpers.py`` 拆分——边框块构建
为独立职责（纯函数，依赖 ``output.Line``/``core.style.Style`` 与
``_runs_utils.truncate_runs``）。
"""

from __future__ import annotations

from .output import Line, StyledRun
from src.tui.core.style import Style
from ._runs_utils import truncate_runs


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


__all__ = ["build_border_box"]
