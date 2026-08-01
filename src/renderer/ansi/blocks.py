"""块级渲染 — 标题/列表/引用/告示/折叠块 → AnsiLine。

处理 TokenType：PARAGRAPH / HEADING / HR / BLOCKQUOTE / LIST_ITEM /
DEFINITION_ITEM / ADMONITION / DETAILS / EMPTY_LINE / FENCED_DIV。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from .inline import render_inline

# ── 块级样式常量 ──────────────────────────────────────────
_HEADING_STYLES: list[Style] = [
    Style(fg=45, bold=True),    # 1 — 亮青
    Style(fg=39, bold=True),    # 2 — 亮蓝
    Style(fg=38, bold=True),    # 3 — 蓝
    Style(fg=242, bold=True),   # 4 — 中灰粗
    Style(fg=242),              # 5
    Style(fg=242),              # 6
]

_STYLE_HR = Style(fg=239)
_STYLE_BQ = Style(fg=242)
_STYLE_LIST_BULLET = Style(fg=214)
_STYLE_LIST_NUMBER = Style(fg=68)
_STYLE_TODO_UNCHECKED = Style(fg=242)
_STYLE_TODO_CHECKED = Style(fg=41)
_STYLE_DEF_TERM = Style(fg=45, bold=True)

# Admonition 类型 → 颜色
_ADMONITION_COLORS: dict[str, Style] = {
    "NOTE": Style(fg=45, bold=True),
    "TIP": Style(fg=41, bold=True),
    "IMPORTANT": Style(fg=68, bold=True),
    "WARNING": Style(fg=220, bold=True),
    "CAUTION": Style(fg=196, bold=True),
    "CITE": Style(fg=242, bold=True),
    "INFO": Style(fg=75, bold=True),
    "SUCCESS": Style(fg=41, bold=True),
    "QUESTION": Style(fg=75, bold=True),
    "BUG": Style(fg=196, bold=True),
    "DANGER": Style(fg=196, bold=True),
}

# 无序列表项目符号（按深度）
_BULLETS = ["\u2022", "\u25e6", "\u25aa"]

# ── 标题 ─────────────────────────────────────────────


def render_heading(token) -> list[AnsiLine]:
    level = int(token.meta.get("level", 1))
    style = _HEADING_STYLES[min(max(level, 1), 6) - 1]
    runs = render_inline(token.content, style)
    line = AnsiLine(runs)
    return [line]


def render_hr(token) -> list[AnsiLine]:
    return [AnsiLine.of("\u2500" * 40, _STYLE_HR)]


# ── 段落 ─────────────────────────────────────────────


def render_paragraph(token) -> list[AnsiLine]:
    # content 中 \\n 为软换行；逐段渲染为独立行
    lines: list[AnsiLine] = []
    current = AnsiLine()
    for i, seg in enumerate(token.content.split("\n")):
        if i > 0:
            lines.append(current)
            current = AnsiLine()
        for run in render_inline(seg):
            current.append_run(run)
    if current.runs or not lines:
        lines.append(current)
    return lines


# ── 列表 ─────────────────────────────────────────────


def render_list_item(token) -> list[AnsiLine]:
    meta = token.meta
    depth = int(meta.get("depth", 1))
    indent = int(meta.get("indent", 0))
    # 缩进按 indent；项目符号按嵌套深度（depth 为 1-based）
    prefix = "  " * max(0, indent)
    if meta.get("bullet"):
        bullet = _BULLETS[min(max(depth - 1, 0), len(_BULLETS) - 1)]
        head = f"{prefix}{bullet} "
        head_style = _STYLE_LIST_BULLET
    else:
        number = meta.get("number", 1)
        head = f"{prefix}{number}. "
        head_style = _STYLE_LIST_NUMBER
    line = AnsiLine.of(head, head_style)
    content = token.content
    # 待办 checkbox
    stripped = content.lstrip()
    if meta.get("todo"):
        checked = meta.get("checked", False)
        checkbox = "[x]" if checked else "[ ]"
        line.append(checkbox + " ", _STYLE_TODO_CHECKED if checked else _STYLE_TODO_UNCHECKED)
        content = stripped[4:].lstrip() if len(stripped) > 4 else ""
    for run in render_inline(content):
        line.append_run(run)
    return [line]


def render_definition_item(token) -> list[AnsiLine]:
    term = token.meta.get("term", "")
    line = AnsiLine.of(f"{term}: ", _STYLE_DEF_TERM)
    for run in render_inline(token.content):
        line.append_run(run)
    return [line]


# ── 引用 ─────────────────────────────────────────────


def render_blockquote(token, depth: int = 0) -> list[AnsiLine]:
    content = token.content if hasattr(token, "content") else ""
    lines: list[AnsiLine] = []
    for i, seg in enumerate(str(content).split("\n")):
        prefix = "\u2502 " * max(1, depth + 1)
        line = AnsiLine.of(prefix, _STYLE_BQ)
        for run in render_inline(seg):
            line.append_run(run)
        lines.append(line)
    return lines


# ── Admonition ───────────────────────────────────────


def render_admonition(token) -> list[AnsiLine]:
    atype = str(token.meta.get("type", "NOTE")).upper()
    color = _ADMONITION_COLORS.get(atype, _STYLE_LIST_BULLET)
    lines: list[AnsiLine] = []
    # 首行：标注标签 + 正文首行
    parts = str(token.content).split("\n")
    head = AnsiLine.of(f"\u25a0 {atype} ", color)
    for run in render_inline(parts[0]):
        head.append_run(run)
    lines.append(head)
    for seg in parts[1:]:
        body = AnsiLine.of("    ", _STYLE_BQ)
        for run in render_inline(seg):
            body.append_run(run)
        lines.append(body)
    return lines


# ── 折叠块（DETAILS） ────────────────────────────────


def render_details(token) -> list[AnsiLine]:
    summary = token.meta.get("summary", "")
    head = AnsiLine.of("\u25b6 ", _STYLE_LIST_BULLET)
    for run in render_inline(str(summary)):
        head.append_run(run)
    return [head]


# ── FencedDiv ────────────────────────────────────────


def render_fenced_div(token) -> list[AnsiLine]:
    dtype = str(token.meta.get("type", "NOTE")).upper()
    color = _ADMONITION_COLORS.get(dtype, _STYLE_LIST_BULLET)
    head = AnsiLine.of(f"\u25aa {dtype} ", color)
    if token.content:
        for run in render_inline(str(token.content)):
            head.append_run(run)
    return [head]


# ── 空行 ─────────────────────────────────────────────


def render_empty_line(token) -> list[AnsiLine]:
    return [AnsiLine.of("")]


__all__ = [
    "render_heading",
    "render_hr",
    "render_paragraph",
    "render_list_item",
    "render_definition_item",
    "render_blockquote",
    "render_admonition",
    "render_details",
    "render_fenced_div",
    "render_empty_line",
]
