"""轻量级纯 Python Markdown 渲染器 — 将 Markdown 文本转为 ANSI 样式字符串。

不引入任何第三方依赖，仅使用 Python 标准库和项目内部的 ansi.style() 函数。
采用逐行状态机模式：normal / fenced_code / list 三种状态。

使用示例:
    from ..infrastructure.markdown_renderer import render_markdown
    print(render_markdown("## Hello\n\n**bold** and *italic*"))

性能: 单次渲染限制最大 200 行，超出截断。
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from .ansi import style, ANSI_RESET, ANSI_BOLD, ANSI_DIM, ANSI_ITALIC

_logger = logging.getLogger(__name__)

# ── 渲染限制 ─────────────────────────────────────────────
_MAX_LINES = 200  # 最大渲染行数，超出截断

# ── 水平线字符 ───────────────────────────────────────────
_HR_CHAR = "\u2500"  # ─ (U+2500 BOX DRAWINGS LIGHT HORIZONTAL)
_HR_LEN = 60

# ── 内联 Markdown 正则 ───────────────────────────────────
# 注意：处理顺序很重要——代码块先于粗体/斜体
_RE_INLINE_CODE = re.compile(r'(?<!`)`([^`]+?)`(?!`)')
_RE_BOLD = re.compile(r'\*\*(.+?)\*\*')
_RE_ITALIC = re.compile(r'(?<!\*)\*([^*\n]+?)\*(?!\*)')

# ── 块级行模式正则 ──────────────────────────────────────
_RE_HEADING = re.compile(r'^(#{1,3})\s+(.+)$')
_RE_UNORDERED_LIST = re.compile(r'^(\s*)[-*+]\s+(.+)$')
_RE_ORDERED_LIST = re.compile(r'^(\s*)(\d+)\.\s+(.+)$')
_RE_BLOCKQUOTE = re.compile(r'^>\s?(.*)$')
_RE_HR = re.compile(r'^[-*_]{3,}\s*$')
_RE_FENCE_START = re.compile(r'^```(\S*)$')
_RE_FENCE_END = re.compile(r'^```\s*$')

# ── 状态机状态 ──────────────────────────────────────────
_STATE_NORMAL = "normal"
_STATE_FENCED = "fenced_code"
_STATE_LIST = "list"


def render_markdown(text: str, width: int = 80) -> str:
    """将 Markdown 文本渲染为 ANSI 样式字符串。

    Args:
        text: Markdown 原始文本。
        width: 终端宽度（用于水平线长度），默认 80。

    Returns:
        带 ANSI 样式转义序列的字符串，可直接输出到终端。
    """
    try:
        return _render_markdown_impl(text, width)
    except Exception:
        _logger.debug("Markdown 渲染失败，回退为纯文本输出", exc_info=True)
        return text


def _render_markdown_impl(text: str, width: int) -> str:
    """render_markdown 的内部实现，由 render_markdown 的 try/except 包裹调用。"""
    if not text:
        return ""

    lines = text.split("\n")
    # 超过最大行数截断
    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
        truncated_notice = style(
            f"\n... (截断，仅渲染前 {_MAX_LINES} 行)",
            dim=True, italic=True,
        )
    else:
        truncated_notice = ""

    state = _STATE_NORMAL
    result_parts: list[str] = []
    fence_lang: str = ""
    fence_lines: list[str] = []
    hr_len = min(width - 4, _HR_LEN)

    for line in lines:
        if state == _STATE_FENCED:
            if _RE_FENCE_END.match(line):
                # 代码块结束
                result_parts.append(_render_fenced_block(fence_lang, fence_lines))
                fence_lang = ""
                fence_lines = []
                state = _STATE_NORMAL
                result_parts.append("")  # 空行分隔
            else:
                fence_lines.append(line)
            continue

        # ── normal / list 状态下检测 fence 起始 ──
        m_fence = _RE_FENCE_START.match(line)
        if m_fence:
            state = _STATE_FENCED
            fence_lang = m_fence.group(1) or ""
            fence_lines = []
            continue

        # ── 水平线 ──
        if _RE_HR.match(line):
            hr_line = _HR_CHAR * hr_len
            result_parts.append(style(hr_line, dim=True))
            state = _STATE_NORMAL
            continue

        # ── 标题 ──
        m_h = _RE_HEADING.match(line)
        if m_h:
            level = len(m_h.group(1))
            heading_text = m_h.group(2)
            # 渲染标题内联元素后，将内层 ANSI_RESET 替换为 RESET+重应用 bold，
            # 防止内层 RESET 清除外层 bold 样式
            heading_text = _render_inline(heading_text).replace(
                ANSI_RESET, f"{ANSI_RESET}{ANSI_BOLD}"
            )
            if level == 1:
                prefix = "\n▌ "
                result_parts.append(style(f"{prefix}{heading_text}", bold=True))
            elif level == 2:
                result_parts.append(style(f"\n  {heading_text}", bold=True))
            else:
                result_parts.append(style(f"\n    {heading_text}", bold=True))
            state = _STATE_NORMAL
            continue

        # ── 块引用 ──
        m_bq = _RE_BLOCKQUOTE.match(line)
        if m_bq:
            bq_text = m_bq.group(1)
            # 将内层 ANSI_RESET 替换为 RESET+重应用 dim+italic，
            # 防止内层 RESET 清除外层块引用样式
            bq_text = _render_inline(bq_text).replace(
                ANSI_RESET, f"{ANSI_RESET}{ANSI_DIM}{ANSI_ITALIC}"
            )
            result_parts.append(style(f"  │ {bq_text}", dim=True, italic=True))
            state = _STATE_NORMAL
            continue

        # ── 无序列表 ──
        m_ul = _RE_UNORDERED_LIST.match(line)
        if m_ul:
            indent = len(m_ul.group(1))
            indent_str = "  " * (indent // 2)
            item_text = m_ul.group(2)
            item_text = _render_inline(item_text)
            result_parts.append(f"{indent_str}  • {item_text}")
            state = _STATE_LIST
            continue

        # ── 有序列表 ──
        m_ol = _RE_ORDERED_LIST.match(line)
        if m_ol:
            indent = len(m_ol.group(1))
            indent_str = "  " * (indent // 2)
            num = m_ol.group(2)
            item_text = m_ol.group(3)
            item_text = _render_inline(item_text)
            result_parts.append(f"{indent_str}  {num}. {item_text}")
            state = _STATE_LIST
            continue

        # ── 列表续行（缩进继续） ──
        if state == _STATE_LIST and line.startswith("  "):
            result_parts.append(f"    {line.strip()}")
            continue

        # ── 空行：退出 list 状态 ──
        if line.strip() == "":
            result_parts.append("")
            state = _STATE_NORMAL
            continue

        # ── 普通段落 ──
        result_parts.append(_render_inline(line))
        state = _STATE_NORMAL

    # 未闭合的 fence
    if state == _STATE_FENCED and fence_lines:
        result_parts.append(_render_fenced_block(fence_lang, fence_lines))

    return "\n".join(result_parts) + truncated_notice


def _render_inline(text: str) -> str:
    """渲染行内 Markdown 元素：行内代码、粗体、斜体。

    处理顺序：行内代码（优先级最高）→ 粗体 → 斜体。
    使用占位符保护已处理的片段，防止后续正则误匹配。

    Args:
        text: 单行文本（可能含 **bold**、*italic*、`code`）。

    Returns:
        带 ANSI 样式转义序列的字符串。
    """
    placeholders: list[str] = []

    def _save(fn: Callable[[str], str], match: re.Match) -> str:
        idx = len(placeholders)
        rendered = fn(match.group(1))
        placeholders.append(rendered)
        return f"\x00{idx}\x00"

    # 步骤 1：行内代码 → dim+reverse
    text = _RE_INLINE_CODE.sub(
        lambda m: _save(
            lambda t: style(t, dim=True, reverse=True), m
        ), text
    )

    # 步骤 2：粗体 **text** → bold
    text = _RE_BOLD.sub(
        lambda m: _save(
            lambda t: style(t, bold=True), m
        ), text
    )

    # 步骤 3：斜体 *text* → italic
    text = _RE_ITALIC.sub(
        lambda m: _save(
            lambda t: style(t, italic=True), m
        ), text
    )

    # 恢复占位符
    for i, ph in enumerate(placeholders):
        text = text.replace(f"\x00{i}\x00", ph)

    return text


def _render_fenced_block(lang: str, lines: list[str]) -> str:
    """渲染围栏代码块为 ANSI 样式字符串。

    代码块使用 dim 样式，语言标签使用 cyan 颜色。
    代码内容每行缩进 4 空格以区别于正文。

    Args:
        lang: 语言标识符（可为空）。
        lines: 代码行列表。

    Returns:
        ANSI 样式字符串。
    """
    parts: list[str] = []

    # ── 顶部边框 + 语言标签 ──
    top_border = _HR_CHAR * 4
    if lang:
        lang_label = style(lang, fg="cyan")
        parts.append(style(f"{top_border} {lang_label} ", dim=True))
    else:
        parts.append(style(f"{top_border} code ", dim=True))

    # ── 代码内容（dim 样式，每行缩进 4 空格） ──
    for line in lines:
        parts.append(style(f"    {line}", dim=True))

    # ── 底部边框 ──
    parts.append(style(top_border, dim=True))

    return "\n".join(parts)
