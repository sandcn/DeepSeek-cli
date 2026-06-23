"""纯 Python ANSI 转义序列工具模块。

提供终端颜色、样式修饰符、光标控制和行操作的 ANSI 转义序列。
无第三方依赖，仅使用 Python 标准库。

使用示例:
    from ._ansi import style, style_reset
    print(f"{style('hello', fg='red', bold=True)} world")
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# ── ANSI 基础 ────────────────────────────────────────────
_CSI = "\033["
_RESET = f"{_CSI}0m"

# ── 16 标准色调色板 ──────────────────────────────────────
_COLORS_16: dict[str, int] = {
    "black": 0, "red": 1, "green": 2, "yellow": 3,
    "blue": 4, "magenta": 5, "cyan": 6, "white": 7,
    "bright_black": 60, "bright_red": 61, "bright_green": 62,
    "bright_yellow": 63, "bright_blue": 64, "bright_magenta": 65,
    "bright_cyan": 66, "bright_white": 67,
}

# ── 样式修饰符 ───────────────────────────────────────────
_STYLES: dict[str, int] = {
    "bold": 1, "dim": 2, "italic": 3, "underline": 4,
    "blink": 5, "reverse": 7, "hidden": 8, "strikethrough": 9,
}

# ── 公开 ANSI 颜色字符串 ─────────────────────────────────
ANSI_RED = f"{_CSI}31m"
ANSI_GREEN = f"{_CSI}32m"
ANSI_YELLOW = f"{_CSI}33m"
ANSI_BLUE = f"{_CSI}34m"
ANSI_MAGENTA = f"{_CSI}35m"
ANSI_CYAN = f"{_CSI}36m"
ANSI_WHITE = f"{_CSI}37m"
ANSI_RESET = _RESET

# 样式
ANSI_BOLD = f"{_CSI}1m"
ANSI_DIM = f"{_CSI}2m"
ANSI_ITALIC = f"{_CSI}3m"
ANSI_UNDERLINE = f"{_CSI}4m"
ANSI_REVERSE = f"{_CSI}7m"

# ── 光标移动 ─────────────────────────────────────────────
def cursor_up(n: int = 1) -> str:
    """光标上移 n 行。"""
    return f"{_CSI}{n}A" if n > 0 else ""


def cursor_down(n: int = 1) -> str:
    """光标下移 n 行。"""
    return f"{_CSI}{n}B" if n > 0 else ""


def cursor_forward(n: int = 1) -> str:
    """光标右移 n 列。"""
    return f"{_CSI}{n}C" if n > 0 else ""


def cursor_back(n: int = 1) -> str:
    """光标左移 n 列。"""
    return f"{_CSI}{n}D" if n > 0 else ""


def cursor_move_to(row: int, col: int) -> str:
    """光标移动到指定行列（1-based）。"""
    return f"{_CSI}{row};{col}H"


def cursor_move_to_column(col: int) -> str:
    """光标移动到当前行的指定列（1-based）。"""
    return f"{_CSI}{col}G"


# ── 行操作 ───────────────────────────────────────────────
def clear_line() -> str:
    """清除当前整行。"""
    return f"{_CSI}2K"


def clear_to_end() -> str:
    """清除从光标到行尾。"""
    return f"{_CSI}0K"


def clear_to_start() -> str:
    """清除从行首到光标。"""
    return f"{_CSI}1K"


def clear_screen() -> str:
    """清除整个屏幕（保持滚动缓冲区）。"""
    return f"{_CSI}2J"


def clear_screen_from_cursor() -> str:
    """清除从光标到屏幕末尾。"""
    return f"{_CSI}0J"


# ── 光标可见性 ───────────────────────────────────────────
def cursor_show() -> str:
    """显示光标。"""
    return f"{_CSI}?25h"


def cursor_hide() -> str:
    """隐藏光标。"""
    return f"{_CSI}?25l"


# ── 屏幕缓冲区 ───────────────────────────────────────────
def save_cursor() -> str:
    """保存光标位置。"""
    return f"{_CSI}s"


def restore_cursor() -> str:
    """恢复光标位置。"""
    return f"{_CSI}u"


# ── 样式构建函数 ─────────────────────────────────────────

def _fg_code(color: str) -> str:
    """将颜色名转为前景色 ANSI 码。

    支持: 16色名 ('red', 'green', ...)、'bright_*' 变体、
    '#RRGGBB' 格式（转为 256 色近似）。"""
    if color in _COLORS_16:
        c = _COLORS_16[color]
        if c < 8:
            return f"{_CSI}{30 + c}m"
        else:
            return f"{_CSI}{90 + c - 60}m"  # bright → 90-97 标准码
    # '#RRGGBB' → 256 色近似
    if color.startswith("#") and len(color) == 7:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        idx = 16 + 36 * (r // 51) + 6 * (g // 51) + (b // 51)
        return f"{_CSI}38;5;{idx}m"
    _logger.debug("未知颜色名: %s", color)
    return ""


def _bg_code(color: str) -> str:
    """将颜色名转为背景色 ANSI 码。"""
    if color in _COLORS_16:
        c = _COLORS_16[color]
        if c < 8:
            return f"{_CSI}{40 + c}m"
        else:
            return f"{_CSI}{100 + c - 60}m"  # bright → 100-107 标准码
    if color.startswith("#") and len(color) == 7:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        idx = 16 + 36 * (r // 51) + 6 * (g // 51) + (b // 51)
        return f"{_CSI}48;5;{idx}m"
    _logger.debug("未知背景色名: %s", color)
    return ""


def style(text: str = "", *, fg: str | None = None, bg: str | None = None,
          bold: bool = False, dim: bool = False, italic: bool = False,
          underline: bool = False, reverse: bool = False,
          strikethrough: bool = False) -> str:
    """用 ANSI 转义序列包裹文本。

    Args:
        text: 要样式化的文本
        fg: 前景色名（如 'red', 'green', '#FF0000'）
        bg: 背景色名
        bold/dim/italic/underline/reverse/strikethrough: 样式标志

    Returns:
        包裹了 ANSI 序列的字符串，末尾带重置码。
    """
    codes: list[str] = []
    if fg:
        codes.append(_fg_code(fg))
    if bg:
        codes.append(_bg_code(bg))
    if bold:
        codes.append(ANSI_BOLD)
    if dim:
        codes.append(ANSI_DIM)
    if italic:
        codes.append(ANSI_ITALIC)
    if underline:
        codes.append(ANSI_UNDERLINE)
    if reverse:
        codes.append(ANSI_REVERSE)
    if strikethrough:
        codes.append(f"{_CSI}9m")

    if not codes:
        return text

    prefix = "".join(codes)
    return f"{prefix}{text}{_RESET}"


def style_reset(text: str = "") -> str:
    """仅追加重置码（用于手动管理样式区间）。"""
    return f"{text}{_RESET}"


# ── 便捷颜色函数 ─────────────────────────────────────────

def red(text: str) -> str:       return style(text, fg="red")
def green(text: str) -> str:     return style(text, fg="green")
def yellow(text: str) -> str:    return style(text, fg="yellow")
def blue(text: str) -> str:      return style(text, fg="blue")
def magenta(text: str) -> str:   return style(text, fg="magenta")
def cyan(text: str) -> str:      return style(text, fg="cyan")
def white(text: str) -> str:     return style(text, fg="white")
def black(text: str) -> str:     return style(text, fg="black")


def bright_red(text: str) -> str:      return style(text, fg="bright_red")
def bright_green(text: str) -> str:    return style(text, fg="bright_green")
def bright_yellow(text: str) -> str:   return style(text, fg="bright_yellow")
def bright_blue(text: str) -> str:     return style(text, fg="bright_blue")
def bright_magenta(text: str) -> str:  return style(text, fg="bright_magenta")
def bright_cyan(text: str) -> str:     return style(text, fg="bright_cyan")
def bright_white(text: str) -> str:    return style(text, fg="bright_white")


def bold(text: str) -> str:       return style(text, bold=True)
def dim(text: str) -> str:        return style(text, dim=True)
def italic(text: str) -> str:     return style(text, italic=True)
def underline(text: str) -> str:  return style(text, underline=True)
