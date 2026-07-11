"""
ANSI 颜色常量（纯色值，无 Console）

始终输出 ANSI 颜色转义序列，不依赖 TTY 检测。
"""
from __future__ import annotations

import re

try:
    from wcwidth import wcswidth as _wcswidth
    _HAS_WCWIDTH = True
except ImportError:  # pragma: no cover
    _HAS_WCWIDTH = False

# ── ANSI 颜色常量（从 core.constants 导入，消除重复定义） ──
from ..core.constants import (
    GRAY, WHITE, CYAN, GREEN, YELLOW, RED, BLUE, MAGENTA,
    BOLD, DIM, RESET, ITALIC, UNDERLINE,
    BRIGHT_CYAN, BRIGHT_GREEN, BRIGHT_YELLOW, BRIGHT_BLUE,
    BRIGHT_MAGENTA, BRIGHT_RED, BRIGHT_WHITE, BRIGHT_BLACK,
    BG_BLUE, BG_CYAN, BG_GREEN, BG_YELLOW,
    ORANGE, TEAL, PINK, LAVENDER,
    SOFT_GREEN, SOFT_BLUE, SOFT_YELLOW, DARK_GRAY,
)

# ── ANSI 转义序列清洗 ───────────────────────────────────
# 完整版：覆盖 CSI/OSC/DCS/APC 全部 ANSI 序列
_ANSI_CLEAN_RE = re.compile(
    r'\x1B(?:'
    r'[@-Z\\-_]|'
    r'\[[0-?]*[ -/]*[@-~]|'
    r'[PX^_].*?(?:\x1b\\|\x07)'
    r')'
)


def strip_ansi(text: str) -> str:
    """移除字符串中的所有 ANSI 转义序列"""
    return _ANSI_CLEAN_RE.sub('', text)


# ── ANSI 视觉宽度工具 ───────────────────────────────────

_ANSI_SEQ_RE = re.compile(r'\033\[[\d;]*[a-zA-Z]')


def _char_width(ch: str) -> int:
    """返回单个字符的终端视觉宽度。

    使用 wcwidth.wcswidth 计算字符宽度（CJK=2，ASCII=1，组合符=0）。
    wcswidth 对不可打印字符返回 -1，此时回退为 1。
    wcwidth 库不可用时回退到 ``ord(ch) > 127`` 判断（粗略近似）。

    Args:
        ch: 单个字符。

    Returns:
        字符的视觉宽度（0、1 或 2）。
    """
    if _HAS_WCWIDTH:
        w = _wcswidth(ch)
        return w if w >= 0 else 1
    return 2 if ord(ch) > 127 else 1


def visual_width(text: str) -> int:
    """计算文本的终端视觉宽度（中文字符=2，ASCII=1，忽略 ANSI 转义码）。"""
    return sum(_char_width(ch) for ch in strip_ansi(text))


def truncate_ansi_visual(text: str, max_visual: int) -> str:
    """截断带 ANSI 转义码的文本，保留样式，确保视觉宽度不超过 max_visual。

    Args:
        text: 带可能 ANSI 转义码的文本
        max_visual: 最大视觉宽度（终端列数）

    Returns:
        截断后的文本（视觉宽度 ≤ max_visual），超出时末尾追加 … + RESET
    """
    if visual_width(text) <= max_visual:
        return text
    result: list[str] = []
    vw = 0
    pos = 0
    while pos < len(text) and vw < max_visual - 1:  # 留 1 列给 …
        m = _ANSI_SEQ_RE.match(text, pos)
        if m:
            result.append(text[pos:m.end()])
            pos = m.end()
        else:
            ch = text[pos]
            cw = _char_width(ch)
            if vw + cw > max_visual - 1:
                break
            result.append(ch)
            vw += cw
            pos += 1
    result.append('…')
    result.append(RESET)
    return ''.join(result)


# ── ANSI SGR 工具（从 status_bar 提升，消除重复） ──────


def skip_ansi_sgr(text: str, i: int) -> int:
    """如果 text[i] 处是 ANSI SGR 转义序列（\\033[...m），跳过并返回新位置。

    SGR（Select Graphic Rendition）序列格式固定为 \\033[...m，
    与完整 CSI 序列不同（不含中间字节 [ -/]*）。

    Returns:
        跳过序列后的新索引；如果不是 ANSI 序列起始位置则返回原 i。
    """
    n = len(text)
    if i < n and text[i] == "\033" and i + 1 < n and text[i + 1] == "[":
        i += 2
        while i < n and text[i] != "m":
            i += 1
        if i < n:
            i += 1
    return i


def truncate_ansi_sgr(text: str, max_width: int, *, from_end: bool = False) -> str:
    """ANSI SGR 转义序列感知的字符串截断。

    仅计算可见字符的视觉宽度（跳过 \\033[...m 序列，使用 wcswidth），
    达到 max_width 时截断，并在末尾追加 \\033[0m 重置样式。

    与 truncate_ansi_visual 的区别：
    - truncate_ansi_visual：处理完整 ANSI 序列（CSI/OSC/DCS），超出时追加 … + RESET
    - truncate_ansi_sgr：仅处理 SGR 序列（\\033[...m），超出时追加 \\033[0m

    两者均使用 _char_width（wcswidth）计算字符视觉宽度。

    Args:
        text: 含 ANSI 转义序列的原始文本。
        max_width: 最大可见视觉宽度（终端列数）。
        from_end: True 时取最后 max_width 列可见字符（用于右侧截断）。

    Returns:
        截断后的文本（末尾追加 \\033[0m）。
    """
    if from_end:
        visible_positions: list[int] = []
        visible_widths: list[int] = []
        i = 0
        n = len(text)
        while i < n:
            prev = i
            i = skip_ansi_sgr(text, i)
            if i == prev:
                visible_positions.append(i)
                visible_widths.append(_char_width(text[i]))
                i += 1
        total_width = sum(visible_widths)
        if total_width <= max_width:
            return text + "\033[0m"
        # 从末尾向前累加视觉宽度，找到不超过 max_width 的起始位置
        accumulated = 0
        start_idx = len(visible_positions)
        while start_idx > 0 and accumulated + visible_widths[start_idx - 1] <= max_width:
            start_idx -= 1
            accumulated += visible_widths[start_idx]
        start = visible_positions[start_idx] if start_idx < len(visible_positions) else n
        return text[start:] + "\033[0m"

    visible = 0
    i = 0
    n = len(text)
    while i < n:
        prev = i
        i = skip_ansi_sgr(text, i)
        if i == prev:
            cw = _char_width(text[i])
            if visible + cw > max_width:
                break
            visible += cw
            i += 1
    return text[:i] + "\033[0m"


# ── ANSI-aware 行截断（终端宽度自适应） ─────────────────


def truncate_ansi_line(text: str, max_width: int) -> str:
    """ANSI 安全截断：按可见视觉宽度截断含转义序列的字符串，保留颜色码并在末尾追加 RESET + ...。

    仅当 visible 文本视觉宽度超过 max_width 时才截断，否则原样返回。

    Args:
        text: 含 ANSI 转义序列的原始行
        max_width: 最大可见视觉宽度（终端列数，含 ... 占位）

    Returns:
        截断后的字符串（以 RESET + '...' 结尾），或原字符串（无需截断时）
    """
    if visual_width(text) <= max_width:
        return text

    visible_limit = max_width - 3
    if visible_limit < 1:
        visible_limit = max_width
    result: list[str] = []
    visible = 0
    pos = 0
    while pos < len(text):
        m = _ANSI_CLEAN_RE.match(text, pos)
        if m:
            result.append(m.group())
            pos = m.end()
        else:
            cw = _char_width(text[pos])
            if visible + cw > visible_limit:
                break
            result.append(text[pos])
            visible += cw
            pos += 1
    result.append(RESET)
    result.append('...')
    return ''.join(result)


__all__: list[str] = [
    # 颜色常量（从 core.constants re-export，向后兼容）
    "GRAY", "WHITE", "CYAN", "GREEN", "YELLOW", "RED", "BLUE", "MAGENTA",
    "BOLD", "DIM", "RESET", "ITALIC", "UNDERLINE",
    "BRIGHT_CYAN", "BRIGHT_GREEN", "BRIGHT_YELLOW", "BRIGHT_BLUE",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_BLACK",
    "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_YELLOW",
    "ORANGE", "TEAL", "PINK", "LAVENDER",
    "SOFT_GREEN", "SOFT_BLUE", "SOFT_YELLOW", "DARK_GRAY",
    # 工具函数
    "strip_ansi", "visual_width", "truncate_ansi_visual",
    "skip_ansi_sgr", "truncate_ansi_sgr",
    "truncate_ansi_line",
]
