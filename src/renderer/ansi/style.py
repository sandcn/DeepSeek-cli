"""ansi 最小样式 — 自包含 Style（renderer 不依赖 tui）。

``src/renderer/`` 层不允许模块级导入 ``src.tui``（方向校验见
tests/test_renderer/test_locks_location.py）。故本包自带最小 Style：
  - fg/bg 可为调色板索引（int，xterm-256）或 24-bit RGB 三元组（tuple）。
  - to_ansi() / apply() / merge() 与 tui.core.Style 语义一致。
  - 颜色输出**恒为 24-bit 真彩**：tuple 直接输出 ``38;2``/``48;2``，int 按
    xterm-256 调色板反查精确 RGB 后输出 ``38;2``/``48;2``（2026-10-25 用户
    需求：删除原 256 色降级路径）；``NO_COLOR`` 存在时禁用颜色（空串）。
  - 宽度一律用 renderer._utils.cjk_display_width（renderer 内自含）。
"""

from __future__ import annotations

import os
from typing import Union

from src._compat import dataclass

# 颜色值：调色板索引（int，xterm-256）或 (r,g,b) 24-bit 三元组
ColorValue = Union[int, tuple[int, int, int]]


# ═══════════════════════════════════════════════════════════
# 颜色开关（NO_COLOR 规范；自包含，renderer 不依赖 tui）
# ═══════════════════════════════════════════════════════════
# 2026-10-25（用户需求：颜色全部真彩色，删除原来的 256 色）：终端真彩能力
# 检测（COLORTERM / TERM direct / 降级 256 色）已删除——颜色输出恒为 24-bit
# 真彩。仅保留 ``NO_COLOR`` 规范（存在即禁用颜色）。结果进程级缓存。

_colors_disabled_result: bool | None = None


def colors_disabled() -> bool:
    """``NO_COLOR`` 规范：环境变量存在（含空串）即禁用颜色（进程级缓存）。"""
    global _colors_disabled_result
    if _colors_disabled_result is None:
        _colors_disabled_result = "NO_COLOR" in os.environ
    return _colors_disabled_result


def _reset_color_disabled_cache() -> None:
    """清空 ``NO_COLOR`` 判定缓存（测试用）。"""
    global _colors_disabled_result
    _colors_disabled_result = None


@dataclass(frozen=True, slots=True)
class Style:
    """不可变样式描述器。

    ★ 内存：``slots=True``（无实例 ``__dict__``）——样式对象在长会话的每行
    run 上被大量引用/构造，省下的每实例字典开销可观测。
    """

    fg: ColorValue | None = None
    bg: ColorValue | None = None
    bold: bool = False
    italic: bool = False
    dim: bool = False
    underline: bool = False

    def to_ansi(self) -> str:
        parts: list[str] = []
        if self.bold:
            parts.append("\033[1m")
        if self.dim:
            parts.append("\033[2m")
        if self.italic:
            parts.append("\033[3m")
        if self.underline:
            parts.append("\033[4m")
        if self.fg is not None:
            parts.append(_color_ansi(self.fg, "38"))
        if self.bg is not None:
            parts.append(_color_ansi(self.bg, "48"))
        return "".join(parts)

    def apply(self, text: str) -> str:
        ansi = self.to_ansi()
        if not ansi:
            return text
        return f"{ansi}{text}\033[0m"

    def merge(self, other: "Style") -> "Style":
        return Style(
            fg=other.fg if other.fg is not None else self.fg,
            bg=other.bg if other.bg is not None else self.bg,
            bold=other.bold or self.bold,
            italic=other.italic or self.italic,
            dim=other.dim or self.dim,
            underline=other.underline or self.underline,
        )

    def __bool__(self) -> bool:
        return (
            self.fg is not None
            or self.bg is not None
            or self.bold or self.italic or self.dim or self.underline
        )

def _color_ansi(color: ColorValue, prefix: str) -> str:
    """构建前景/背景 ANSI 序列（恒 24-bit 真彩）。

    - ``tuple`` → ``\\033[{prefix};2;{r};{g};{b}m``（保留原色精度）。
    - ``int``（xterm-256 调色板索引）→ 反查精确 RGB 输出 24-bit。
    - ``NO_COLOR`` 存在 → 空串（禁用颜色）。
    """
    if colors_disabled():
        return ""
    if isinstance(color, tuple):
        r, g, b = color
        return f"\033[{prefix};2;{r};{g};{b}m"
    r, g, b = _XTERM_PALETTE[color]
    return f"\033[{prefix};2;{r};{g};{b}m"

def _build_palette() -> list[tuple[int, int, int]]:
    palette: list[tuple[int, int, int]] = [
        (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
        (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
        (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
        (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
    ]
    for r in range(6):
        for g in range(6):
            for b in range(6):
                palette.append((
                    0 if r == 0 else 55 + r * 40,
                    0 if g == 0 else 55 + g * 40,
                    0 if b == 0 else 55 + b * 40,
                ))
    for i in range(24):
        v = 8 + i * 10
        palette.append((v, v, v))
    return palette

_XTERM_PALETTE: list[tuple[int, int, int]] = _build_palette()

__all__ = [
    "Style", "ColorValue", "colors_disabled", "_reset_color_disabled_cache",
]