"""_rendering._special — 特殊块渲染：Mermaid/Todo/HTML/TOC/渲染统计。"""

from __future__ import annotations

import logging
from collections import Counter

_logger = logging.getLogger(__name__)

from rich.text import Text
from rich.style import Style

from .._utils import cjk_display_width


# ═══════════════════════════════════════════════════════════
# Mermaid 块围栏
# ═══════════════════════════════════════════════════════════

def render_mermaid_block(lang: str) -> Text:
    """渲染 Mermaid 图表围栏（带 dim 样式提示）。"""
    fence_text = f"```{lang}"
    return Text(fence_text, style=Style(dim=True, italic=True))


def render_mermaid_close() -> Text:
    """渲染 Mermaid 图表关闭围栏。"""
    return Text("```", style=Style(dim=True, italic=True))


# ═══════════════════════════════════════════════════════════
# HTML 块框线
# ═══════════════════════════════════════════════════════════

_HTML_BLOCK_TAG_COLORS = {
    "div":   "blue",
    "pre":   "green",
    "table": "yellow",
}


def get_html_tag_color(tag: str) -> str:
    """获取 HTML tag 对应的颜色名。"""
    return _HTML_BLOCK_TAG_COLORS.get(tag, "bright_black")


def render_html_block_open(tag: str, output_width: int) -> Text:
    """渲染 HTML 块打开框线（┌─ <tag> ─...┐）。"""
    color = get_html_tag_color(tag)
    tag_label = f"<{tag}>"
    prefix = f"┌─ {tag_label} ─"
    remaining = output_width - len(prefix) - 1
    if remaining < 3:
        remaining = 3
    bar = prefix + "─" * remaining + "┐"
    return Text(bar, style=Style(color=color, dim=True))


def render_html_block_close(tag: str, output_width: int) -> Text:
    """渲染 HTML 块关闭框线（└─...┘）。"""
    color = get_html_tag_color(tag)
    close_width = cjk_display_width("└┘")
    remaining = output_width - close_width
    if remaining < 0:
        remaining = 0
    bar = "└" + "─" * remaining + "┘"
    return Text(bar, style=Style(color=color, dim=True))


# ═══════════════════════════════════════════════════════════
# 统一目录（TOC）渲染
# ═══════════════════════════════════════════════════════════

def _build_toc_connectors(toc: list[dict]) -> list[dict]:
    """为 TOC 条目构建树形连接符前缀。

    根据条目层级关系计算每条目的树前缀（┣━/┗━/┃），
    使目录以树形结构展示层级关系。

    Args:
        toc: TOC 条目列表

    Returns:
        带 "prefix" 键的条目列表
    """
    if not toc:
        return []

    n = len(toc)
    is_last = [True] * n
    for i in range(n - 1):
        level_i = toc[i]["level"]
        for j in range(i + 1, n):
            if toc[j]["level"] == level_i:
                is_last[i] = False
                break
            elif toc[j]["level"] < level_i:
                break

    ancestors_active: dict[int, bool] = {}
    result = []

    for i, entry in enumerate(toc):
        level = entry["level"]

        prefix_parts = []
        for l in range(1, level):
            if ancestors_active.get(l, False):
                prefix_parts.append("┃  ")
            else:
                prefix_parts.append("   ")

        connector = "┗━ " if is_last[i] else "┣━ "
        prefix_parts.append(connector)

        ancestors_active[level] = not is_last[i]
        for l in list(ancestors_active.keys()):
            if l > level:
                del ancestors_active[l]

        result.append({**entry, "prefix": "".join(prefix_parts)})

    return result


def render_toc(toc: list[dict], output_width: int) -> Text:
    """渲染 Table of Contents（目录），带树形连接符与层级色彩。

    Args:
        toc: TOC 条目列表，每项含 level/text 字段
        output_width: 终端宽度

    Returns:
        渲染好的 Rich Text，空 toc 时返回空 Text
    """
    if not toc:
        return Text()

    result = Text()
    result.append("\n")

    prefix = "┌─ 📑 目录 ─"
    remaining = output_width - cjk_display_width(prefix) - 1
    if remaining < 3:
        remaining = 3
    result.append(prefix + "─" * remaining + "┐\n",
                  style=Style(bold=True, color="bright_cyan"))

    conn_style = Style(dim=True, color="bright_black")
    entries = _build_toc_connectors(toc)

    for entry in entries:
        level = entry["level"]
        text = entry["text"]
        prefix_str = entry["prefix"]

        if level == 1:
            level_style = Style(bold=True, color="bright_yellow")
        elif level == 2:
            level_style = Style(bold=True, color="bright_cyan")
        else:
            level_style = Style(bold=True, color="bright_white", dim=True)

        line = Text(prefix_str, style=conn_style)
        line.append(text, style=level_style)
        line.append("\n")
        result.append_text(line)

    close_width = cjk_display_width("└┘")
    remaining = output_width - close_width
    if remaining < 0:
        remaining = 0
    result.append("└" + "─" * remaining + "┘\n",
                  style=Style(dim=True, color="bright_black"))

    return result


# ═══════════════════════════════════════════════════════════
# 渲染统计摘要
# ═══════════════════════════════════════════════════════════

def render_render_summary(metrics: Counter, token_count: int,
                          elapsed: float, output_width: int) -> Text:
    """渲染渲染统计摘要（用时、Token 数、类型分布）。

    Args:
        metrics: Token 类型计数器
        token_count: 总 Token 数
        elapsed: 渲染总用时（秒）
        output_width: 终端宽度

    Returns:
        渲染好的统计摘要 Rich Text
    """
    if token_count <= 0:
        return Text()

    result = Text()
    result.append("\n", style=Style(dim=True))
    result.append("📊 渲染统计\n", style=Style(bold=True, color="bright_cyan"))
    result.append(f"{'─' * output_width}\n", style=Style(dim=True))

    result.append(f"  总 Token: {token_count}  ", style=Style(bold=True))
    if elapsed > 0:
        result.append(f" 耗时: {elapsed:.2f}s  ", style=Style())
        tps = token_count / elapsed if elapsed > 0 else 0
        result.append(f"速率: {tps:.0f} tokens/s", style=Style(dim=True))
    result.append("\n")

    if metrics:
        top_types = sorted(metrics.items(), key=lambda x: -x[1])[:8]
        for ttype, count in top_types:
            name = ttype.name if hasattr(ttype, 'name') else str(ttype)
            bar_len = max(1, int(count / max(1, top_types[0][1]) * 20))
            bar = "█" * bar_len
            pct = count / token_count * 100
            result.append(
                f"  {name:<20} {count:>4} ({pct:5.1f}%)  {bar}\n",
                style=Style(dim=True),
            )

    result.append(f"{'─' * output_width}\n", style=Style(dim=True))
    return result
