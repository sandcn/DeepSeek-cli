"""ansi 最小样式 — 自包含 Style（renderer 不依赖 tui）。

``src/renderer/`` 层不允许模块级导入 ``src.tui``（方向校验见
tests/test_renderer/test_locks_location.py）。故本包自带最小 Style：
  - fg/bg 可为 256 色号（int）或 24-bit RGB 三元组（tuple）。
  - to_ansi() / apply() / merge() 与 tui.core.Style 语义一致。
  - 颜色输出按终端能力自适应：真彩终端把 int 色号按 xterm-256 调色板反查
    RGB 输出 ``38;2``/``48;2``，tuple 恒 24-bit，其余降级
    ``38;5``/``48;5``（``detect_truecolor`` 与 tui 同口径的环境变量检测）。
  - 宽度一律用 renderer._utils.cjk_display_width（renderer 内自含）。
"""

from __future__ import annotations

import os
from typing import Union

from src._compat import dataclass

# 颜色值：256 色号（int）或 (r,g,b) 24-bit 三元组
ColorValue = Union[int, tuple[int, int, int]]


# ═══════════════════════════════════════════════════════════
# 终端真彩能力（自包含：renderer 不依赖 tui，与 tui.core.color 同口径）
# ═══════════════════════════════════════════════════════════
#: 检测结果进程级缓存（检测一次，进程内不变；测试经 _reset_truecolor_cache 重置）
_truecolor_result: bool | None = None


def detect_truecolor() -> bool:
    """检测终端是否支持 TrueColor（环境变量约定，结果进程级缓存）。

    判定优先级（与 ``src.tui.core.color.detect_truecolor`` 保持一致）：
      1. ``NO_COLOR`` 存在（含空串）→ 强制 256 色降级（最高优先级）。
      2. ``COLORTERM`` 含 "truecolor"/"24bit" → TrueColor。
      3. ``TERM`` 含 "direct" → TrueColor。
      4. 其余 → False（256 色降级）。
    """
    global _truecolor_result
    if _truecolor_result is None:
        _truecolor_result = _detect_truecolor_uncached()
    return _truecolor_result


def _detect_truecolor_uncached() -> bool:
    if "NO_COLOR" in os.environ:
        return False
    ct = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in ct or "24bit" in ct:
        return True
    return "direct" in os.environ.get("TERM", "")


def _reset_truecolor_cache() -> None:
    """清空 TrueColor 检测缓存（测试用）。"""
    global _truecolor_result
    _truecolor_result = None


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
    """构建前景/背景 ANSI 序列（按终端能力输出真彩或 256 色）。

    - ``tuple`` → 24-bit ``\\033[{prefix};2;{r};{g};{b}m``（保留原色精度）。
    - ``int``（256 色号）→ 真彩终端按 xterm-256 调色板反查精确 RGB 输出
      24-bit；不支持真彩（或 ``NO_COLOR``）时降级 ``\\033[{prefix};5;{n}m``。
    """
    if isinstance(color, tuple):
        r, g, b = color
        return f"\033[{prefix};2;{r};{g};{b}m"
    if detect_truecolor():
        r, g, b = _XTERM_PALETTE[color]
        return f"\033[{prefix};2;{r};{g};{b}m"
    return f"\033[{prefix};5;{color}m"

def rgb_to_256(r: int, g: int, b: int) -> int:
    """RGB → 最接近的 xterm-256 色号（自包含实现，带结果缓存）。

    代码高亮对每个 token 做一次 RGB→256 转换，线性遍历 256 色板；同一主题
    的颜色集合有限，缓存后热路径仅一次 dict 查找（流式预览逐行高亮的高频
    调用点显著受益）。
    """
    key = (r, g, b)
    cached = _256_CACHE.get(key)
    if cached is not None:
        return cached
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
    _256_CACHE[key] = best_idx
    return best_idx


#: RGB → 256 色号缓存（颜色集合有限，避免每次线性搜索色板）
_256_CACHE: dict = {}

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
    "Style", "ColorValue", "rgb_to_256",
    "detect_truecolor", "_reset_truecolor_cache",
]