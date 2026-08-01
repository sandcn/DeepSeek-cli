"""ansi 最小样式 — 自包含 Style（renderer 不依赖 tui）。

``src/renderer/`` 层不允许模块级导入 ``src.tui``（方向校验见
tests/test_renderer/test_locks_location.py）。故本包自带最小 Style：
  - fg/bg 可为 256 色号（int）或 24-bit RGB 三元组（tuple）。
  - to_ansi() / apply() / merge() 与 tui.core.Style 语义一致。
  - 宽度一律用 renderer._utils.cjk_display_width（renderer 内自含）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

# 颜色值：256 色号（int）或 (r,g,b) 24-bit 三元组
ColorValue = Union[int, tuple[int, int, int]]


@dataclass(frozen=True)
class Style:
    """不可变样式描述器。"""

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
    """构建 256 色或 24-bit 前景/背景序列。"""
    if isinstance(color, tuple):
        r, g, b = color
        return f"\033[{prefix};2;{r};{g};{b}m"
    return f"\033[{prefix};5;{color}m"


def rgb_to_256(r: int, g: int, b: int) -> int:
    """RGB → 最接近的 xterm-256 色号（自包含实现）。"""
    palette = _XTERM_PALETTE
    best_idx = 0
    best_dist = float("inf")
    for idx, (pr, pg, pb) in enumerate(palette):
        dr = r - pr
        dg = g - pg
        db = b - pb
        dist = dr * dr + dg * dg + db * db
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


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


__all__ = ["Style", "ColorValue", "rgb_to_256"]
