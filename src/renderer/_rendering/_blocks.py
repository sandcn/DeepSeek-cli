"""_rendering._blocks — 块级元素渲染：标题/引用/列表/hr/空行等标准块。"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

from rich.text import Text
from rich.style import Style

from .._utils import cjk_display_width


# ═══════════════════════════════════════════════════════════
# Todo 检测
# ═══════════════════════════════════════════════════════════

def is_todo(text: str) -> tuple[str | None, str]:
    """字符级检测 [ ] 或 [x] 任务列表项。

    Returns:
        (marker, content): marker=' ' 未勾选, marker in 'xX' 已勾选, marker=None 非任务项
    """
    if len(text) >= 4 and text[0] == '[' and text[2] == ']' and text[1] in ' xX':
        return text[1], text[3:].strip()
    return None, text


# ═══════════════════════════════════════════════════════════
# <br> 切分
# ═══════════════════════════════════════════════════════════

def split_by_br(text: str) -> list[str]:
    """按 <br>、<br/>、<br /> 高效切分（用 str.find 跳跃定位）。"""
    parts = []
    last_end = 0
    n = len(text)
    while last_end < n:
        idx = text.find('<br', last_end)
        if idx == -1:
            break
        parts.append(text[last_end:idx])
        after_br = idx + 3
        if after_br < n and text[after_br] == ' ':
            after_br += 1
        if after_br < n and text[after_br] == '/':
            after_br += 1
        if after_br < n and text[after_br] == '>':
            after_br += 1
            last_end = after_br
        else:
            last_end = idx + 3
    parts.append(text[last_end:])
    return parts


# ═══════════════════════════════════════════════════════════
# 标题样式
# ═══════════════════════════════════════════════════════════

_STYLE_HEADING_BOLD = Style(bold=True)
_STYLE_HEADING_H1 = Style(bold=True, underline=True)
_STYLE_HEADING_H2 = Style(bold=True, color="bright_yellow")
_STYLE_HEADING_H3 = Style(bold=True, color="bright_cyan")
_STYLE_HEADING_H4 = Style(bold=True, color="bright_white", dim=True)


def style_heading(text: Text, level: int, output_width: int) -> tuple[Text, int | None]:
    """为标题 Text 应用样式。

    按 level 设置 Rich Style：
      H1 → bold+underline，居中
      H2 → bold+bright_yellow
      H3 → bold+bright_cyan
      H4+ → bold+dimmed bright_white

    Returns:
        (styled_text, padding) — padding 为 H1 的居中填充量，None 表示不需填充
    """
    if level == 1:
        text.stylize(_STYLE_HEADING_H1)
        padding = max(0, (output_width - cjk_display_width(text.plain)) // 2)
        return text, padding
    elif level == 2:
        text.stylize(_STYLE_HEADING_H2)
    elif level == 3:
        text.stylize(_STYLE_HEADING_H3)
    else:
        text.stylize(_STYLE_HEADING_H4)
    return text, None


# ═══════════════════════════════════════════════════════════
# 引用块前缀
# ═══════════════════════════════════════════════════════════

def render_blockquote_prefix(depth: int) -> Text:
    """渲染引用块前缀竖线（▐），一次构造减少 Text 对象分配。"""
    actual_depth = min(depth, 6)
    prefix_str = " " + "▐" * actual_depth + " "
    prefix = Text(prefix_str)
    if actual_depth >= 2:
        dim_start = 2
        dim_end = 1 + actual_depth
        prefix.stylize(Style(dim=True), dim_start, dim_end)
    return prefix


# ═══════════════════════════════════════════════════════════
# 列表项前缀
# ═══════════════════════════════════════════════════════════

_BULLET_SYMBOLS = ["•", "◦", "▪", "▸", "▹", "◆"]
BULLET_SYMBOLS = _BULLET_SYMBOLS


def get_list_item_prefix(depth: int, is_bullet: bool,
                         number: int = 1) -> str:
    """获取列表项缩进和符号前缀字符串。"""
    prefix_spaces = min(depth - 1, 6) * 2 if depth > 1 else 0
    spaces = " " * prefix_spaces
    if is_bullet:
        bullet = _BULLET_SYMBOLS[min(depth - 1, len(_BULLET_SYMBOLS) - 1)]
        return f"{spaces}{bullet} "
    else:
        return f"{spaces}{number}. "


# ═══════════════════════════════════════════════════════════
# 共享渲染辅助函数
# ═══════════════════════════════════════════════════════════

def _get_heading_number(ctx, level: int) -> str:
    """获取标题编号前缀（如 '1.1. '），空字符串表示不编号。"""
    if not ctx or not getattr(ctx, 'heading_numbering', False):
        return ""
    counters = ctx.heading_counters
    for l in range(level + 1, 7):
        counters.pop(l, None)
    counters[level] = counters.get(level, 0) + 1
    parts = []
    for l in range(1, level + 1):
        if l in counters:
            parts.append(str(counters[l]))
        else:
            parts.append("0")
    return ".".join(parts) + "  "


def render_heading(
    text: str,
    level: int,
    output_width: int,
    render_inline_fn=None,
    ctx=None,
) -> tuple[Text, int | None]:
    """渲染标题文本（应用样式 + 可选编号）。

    Returns:
        (styled_text, padding) — padding 为 H1 的居中填充量，None 表示不需填充
    """
    num_prefix = _get_heading_number(ctx, level)
    full_text = num_prefix + text if num_prefix else text
    t = render_inline_fn(full_text) if render_inline_fn else Text(full_text)
    styled, padding = style_heading(t, level, output_width)
    return styled, padding


def render_blockquote(
    content: str,
    depth: int,
    render_inline_fn,
) -> Text:
    """渲染引用块（前缀竖线 + dim 样式内容）。"""
    t = render_inline_fn(content)
    t.stylize(Style(dim=True))
    prefix = render_blockquote_prefix(depth)
    return Text.assemble(prefix, t)


def render_list_item(
    text: str,
    depth: int,
    is_bullet: bool,
    number: int = 1,
    render_inline_fn=None,
) -> Text | None:
    """渲染列表项（含 Todo ☐/☑ 检测）。

    Returns:
        组装好的 Rich Text，或 None（调用方自行处理）
    """
    prefix_spaces = min(depth - 1, 6) * 2 if depth > 1 else 0
    prefix = " " * prefix_spaces

    marker, content = is_todo(text)

    if marker is not None:
        content_rich = render_inline_fn(content) if render_inline_fn else Text(content)
        if marker == ' ':
            symbol = Text("⬜ ", style=Style(color="bright_white"))
            return Text.assemble(prefix, symbol, content_rich)
        else:
            content_rich.stylize(Style(color="green", strike=True))
            symbol = Text("✅ ", style=Style(color="green", bold=True))
            return Text.assemble(prefix, symbol, content_rich)

    bullet = _BULLET_SYMBOLS[min(depth - 1, len(_BULLET_SYMBOLS) - 1)]
    if is_bullet:
        symbol_str = f"{bullet} "
    else:
        symbol_str = f"{number}. "

    content_rich = render_inline_fn(text) if render_inline_fn else Text(text)
    return Text.assemble(prefix, symbol_str, content_rich)


def render_todo_progress_bar(done: int, total: int, width: int = 20) -> Text:
    """渲染增强的 Todo 进度条（██░░░ 样式）。

    Args:
        done: 已完成数
        total: 总数
        width: 进度条字符宽度

    Returns:
        Rich Text 含样式
    """
    if total <= 0:
        return Text("")

    pct = int(done / total * 100)
    filled = int(width * done / total) if total > 0 else 0
    empty = width - filled

    bar = "█" * filled + "░" * empty

    if done == total:
        return Text(
            f"      ✅ {done}/{total} {bar} {pct}%",
            style=Style(color="green", bold=True),
        )
    elif done > 0:
        return Text(
            f"      ☐ {done}/{total} {bar} {pct}%",
            style=Style(color="yellow"),
        )
    else:
        return Text(
            f"      ☐ 0/{total} {bar} 0%",
            style=Style(color="bright_black", dim=True),
        )


def render_definition_item(
    term: str,
    text: str,
    indent: int = 0,
    render_inline_fn=None,
) -> Text:
    """渲染定义列表项。"""
    prefix = "  " * min(indent // 2, 3)
    result = Text()
    if term:
        result.append(f"{prefix}{term}", style=Style(bold=True, color="bright_white"))
        result.append("\n")
        sep_len = min(len(term) + 2, 40)
        result.append(f"{prefix}  {'─' * sep_len}", style=Style(dim=True, color="bright_black"))
        result.append("\n")
        result.append(f"{prefix}  ▸ ", style=Style(color="bright_cyan"))
    else:
        result.append(f"{prefix}  ▸ ", style=Style(color="bright_cyan"))

    content = render_inline_fn(text) if render_inline_fn else Text(text)
    result.append_text(content)
    return result


def render_hr(output_width: int) -> Text:
    """渲染分隔线。"""
    return Text("─" * output_width)


def render_empty_line() -> Text:
    """空行（\n）。"""
    return Text("\n")


# ═══════════════════════════════════════════════════════════
# 样式常量
# ═══════════════════════════════════════════════════════════


