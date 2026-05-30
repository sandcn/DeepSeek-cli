"""
ANSI 颜色常量（纯色值，无 Console）

始终输出 ANSI 颜色转义序列，不依赖 TTY 检测。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

# ── ANSI 颜色常量 ─────────────────────────────────────────
# 所有颜色/样式变量名列表
_COLOR_NAMES = [
    "GRAY", "WHITE", "CYAN", "GREEN", "YELLOW", "RED", "BLUE", "MAGENTA",
    "BOLD", "DIM", "RESET", "ITALIC", "UNDERLINE",
    "BRIGHT_CYAN", "BRIGHT_GREEN", "BRIGHT_YELLOW", "BRIGHT_BLUE",
    "BRIGHT_MAGENTA", "BRIGHT_WHITE",
    "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_YELLOW",
    "ORANGE", "TEAL", "PINK", "LAVENDER",
    "SOFT_GREEN", "SOFT_BLUE", "SOFT_YELLOW", "DARK_GRAY",
]

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


def visual_width(text: str) -> int:
    """计算文本的终端视觉宽度（中文字符=2，ASCII=1，忽略 ANSI 转义码）。"""
    return sum(2 if ord(ch) > 127 else 1 for ch in strip_ansi(text))


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
            cw = 2 if ord(ch) > 127 else 1
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

    仅计算可见字符宽度（跳过 \\033[...m 序列），
    达到 max_width 时截断，并在末尾追加 \\033[0m 重置样式。

    与 truncate_ansi_visual 的区别：
    - truncate_ansi_visual：处理完整 ANSI 序列，中文字符按视觉宽度 2 计算
    - truncate_ansi_sgr：仅处理 SGR 序列，所有可见字符按 1 计算

    Args:
        text: 含 ANSI 转义序列的原始文本。
        max_width: 最大可见字符宽度。
        from_end: True 时取最后 max_width 个可见字符（用于右侧截断）。

    Returns:
        截断后的文本（末尾追加 \\033[0m）。
    """
    if from_end:
        visible_positions: list[int] = []
        i = 0
        n = len(text)
        while i < n:
            prev = i
            i = skip_ansi_sgr(text, i)
            if i == prev:
                visible_positions.append(i)
                i += 1
        if len(visible_positions) <= max_width:
            return text + "\033[0m"
        start = visible_positions[-max_width]
        return text[start:] + "\033[0m"

    visible = 0
    i = 0
    n = len(text)
    while i < n:
        prev = i
        i = skip_ansi_sgr(text, i)
        if i == prev:
            if visible >= max_width:
                break
            visible += 1
            i += 1
    return text[:i] + "\033[0m"


GRAY: str = "\033[90m"
WHITE: str = "\033[37m"
CYAN: str = "\033[36m"
GREEN: str = "\033[32m"
YELLOW: str = "\033[33m"
RED: str = "\033[31m"
BLUE: str = "\033[34m"
MAGENTA: str = "\033[35m"
BOLD: str = "\033[1m"
DIM: str = "\033[2m"
RESET: str = "\033[0m"
ITALIC: str = "\033[3m"
UNDERLINE: str = "\033[4m"
BRIGHT_CYAN: str = "\033[96m"
BRIGHT_GREEN: str = "\033[92m"
BRIGHT_YELLOW: str = "\033[93m"
BRIGHT_BLUE: str = "\033[94m"
BRIGHT_MAGENTA: str = "\033[95m"
BRIGHT_WHITE: str = "\033[97m"
BG_BLUE: str = "\033[44m"
BG_CYAN: str = "\033[46m"
BG_GREEN: str = "\033[42m"
BG_YELLOW: str = "\033[43m"
ORANGE: str = YELLOW
TEAL: str = CYAN
PINK: str = MAGENTA
LAVENDER: str = BRIGHT_MAGENTA
SOFT_GREEN: str = "\033[92m"
SOFT_BLUE: str = "\033[94m"
SOFT_YELLOW: str = "\033[93m"
DARK_GRAY: str = GRAY

# 静态分析辅助：为 globals() 动态赋值的变量提供类型声明，消除静态分析盲区
if TYPE_CHECKING:
    GRAY: str = ""
    WHITE: str = ""
    CYAN: str = ""
    GREEN: str = ""
    YELLOW: str = ""
    RED: str = ""
    BLUE: str = ""
    MAGENTA: str = ""
    BOLD: str = ""
    DIM: str = ""
    RESET: str = ""
    ITALIC: str = ""
    UNDERLINE: str = ""
    BRIGHT_CYAN: str = ""
    BRIGHT_GREEN: str = ""
    BRIGHT_YELLOW: str = ""
    BRIGHT_BLUE: str = ""
    BRIGHT_MAGENTA: str = ""
    BRIGHT_WHITE: str = ""
    BG_BLUE: str = ""
    BG_CYAN: str = ""
    BG_GREEN: str = ""
    BG_YELLOW: str = ""
    ORANGE: str = ""
    TEAL: str = ""
    PINK: str = ""
    LAVENDER: str = ""
    SOFT_GREEN: str = ""
    SOFT_BLUE: str = ""
    SOFT_YELLOW: str = ""
    DARK_GRAY: str = ""

# ── ANSI-aware 行截断（终端宽度自适应） ─────────────────


def truncate_ansi_line(text: str, max_width: int) -> str:
    """ANSI 安全截断：按可见宽度截断含转义序列的字符串，保留颜色码并在末尾追加 RESET + ...。

    仅当 visible 文本超过 max_width 时才截断，否则原样返回。

    Args:
        text: 含 ANSI 转义序列的原始行
        max_width: 最大可见字符数（含 ... 占位）

    Returns:
        截断后的字符串（以 RESET + '...' 结尾），或原字符串（无需截断时）
    """
    if len(strip_ansi(text)) <= max_width:
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
            if visible >= visible_limit:
                break
            result.append(text[pos])
            visible += 1
            pos += 1
    result.append(RESET)
    result.append('...')
    return ''.join(result)


__all__: list[str] = list(_COLOR_NAMES) + [
    "strip_ansi", "visual_width", "truncate_ansi_visual",
    "skip_ansi_sgr", "truncate_ansi_sgr",
    "truncate_ansi_line",
]
