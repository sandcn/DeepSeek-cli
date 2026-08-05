"""_rendering._containers — 容器框架：折叠块/提示块/框线。"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from .._utils import cjk_display_width


# ═══════════════════════════════════════════════════════════
# Details 折叠块
# ═══════════════════════════════════════════════════════════

def render_details_header(depth: int, summary: str, render_inline_fn) -> Text:
    """渲染折叠块标题行（▶ summary）。"""
    indent = "  " * depth
    arrow = Text(f"{indent}▶ ", style=Style(bold=True, color="bright_yellow"))
    if summary:
        summary_text = render_inline_fn(summary) if render_inline_fn else Text(summary)
    else:
        summary_text = Text("展开", style=Style(dim=True, italic=True))
    return Text.assemble(arrow, summary_text)


def render_details_footer(depth: int) -> Text:
    """渲染折叠块关闭标记（───）。"""
    close_indent = "  " * depth
    return Text(f"{close_indent}{'─' * 3}", style=Style(dim=True))


# ═══════════════════════════════════════════════════════════
# Admonition 提示块
# ═══════════════════════════════════════════════════════════

def render_admonition_header(
    adm_type: str,
    content: str,
    output_width: int,
    render_inline_fn=None,
) -> tuple[Text, Text, Text]:
    """渲染告示块标题框线和内容前缀/后缀。

    Returns:
        (header_line, middle_prefix, footer_line)
    """
    from ..admonition import get_admonition_config
    config = get_admonition_config(adm_type.upper())
    color = config["color"]
    icon = config["icon"]
    label = config["label"]

    header_text = f" {icon} {label} "
    if content:
        header_text += f"│ {content}"

    t = Text("  ┌─", style=Style(bold=True, color=color))
    t.append(header_text, style=Style(bold=True, color=color))
    remaining = output_width - cjk_display_width(t.plain) - 1
    if remaining > 0:
        t.append("─" * remaining, style=Style(color=color, dim=True))
    t.append("┐", style=Style(color=color, dim=True))

    middle_prefix = Text("  │ ", style=Style(color=color))

    remaining = output_width - 5
    if remaining > 0:
        footer = Text(f"  └─{'─' * remaining}┘", style=Style(color=color, dim=True))
    else:
        footer = Text("  └─┘", style=Style(color=color, dim=True))

    return t, middle_prefix, footer


# ═══════════════════════════════════════════════════════════
# 通用框线渲染（admonition / fenced_div 共用）
# ═══════════════════════════════════════════════════════════

def render_box_open(prefix: str, text: str, color: str, output_width: int) -> Text:
    """渲染统一的框线打开行。

    Args:
        prefix: 前缀文本（如 "⚠ NOTE" 或 ":: TYPE"）
        text: 标题内容（如有）
        color: 框线颜色
        output_width: 终端宽度

    Returns:
        渲染好的打开框线 Text
    """
    header_text = f" {prefix} "
    if text:
        header_text += f"│ {text}"

    t = Text("  ┌─", style=Style(bold=True, color=color))
    t.append(header_text, style=Style(bold=True, color=color))
    remaining = max(0, output_width - cjk_display_width(t.plain) - 1)
    if remaining > 0:
        t.append("─" * remaining, style=Style(color=color, dim=True))
    t.append("┐", style=Style(color=color, dim=True))
    return t


def render_box_line_prefix(color: str) -> Text:
    """渲染统一的框线内容行前缀。

    Args:
        color: 竖线颜色

    Returns:
        "  │ " 带颜色的 Text
    """
    return Text("  │ ", style=Style(color=color))


def render_cite_prefix() -> Text:
    """渲染 CITE 引用块前缀（📖 图标 + 引用标记）。"""
    return Text("  📖 引用", style=Style(dim=True, bold=True, color="bright_black"))


def render_box_close(color: str, output_width: int) -> Text:
    """渲染统一的框线关闭行。

    Args:
        color: 框线颜色
        output_width: 终端宽度

    Returns:
        渲染好的关闭框线 Text
    """
    remaining = max(0, output_width - 5)
    if remaining > 0:
        return Text(f"  └─{'─' * remaining}┘", style=Style(color=color, dim=True))
    return Text("  └─┘", style=Style(color=color, dim=True))
