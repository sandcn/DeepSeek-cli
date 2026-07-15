"""Color256 值对象与颜色转换模块（Layer 0 Core）。

提供：
  - Color256: 256 色值对象（冻结 slots dataclass），带校验/转换/运算
  - RGB: RGB 颜色值对象
  - hex_to_rgb / rgb_to_256 / lerp_color: 颜色转换纯函数（@lru_cache 缓存）
  - GradientDescriptor: 渐变描述值对象（延迟导入避免循环依赖）

设计原则：
  - 纯函数/值对象：无副作用，不可变
  - 缓存热点：rgb_to_256 / lerp_color 使用 @lru_cache 减少重复计算
  - 延迟导入：resolve() / resolve_with_effect() 方法内部延迟导入，
    避免模块加载循环（core 层不依赖上层模块）
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ── xterm 调色板（从 gradient 共享模块导入） ──
from .gradient import _build_xterm_palette, _XTERM_PALETTE


# ═══════════════════════════════════════════════════════════
# RGB 值对象
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RGB:
    """RGB 颜色值对象。

    三个分量 r/g/b 各为 [0, 255] 范围的整数。
    冻结 dataclass，不可变。

    Attributes:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。
    """
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        """校验分量范围，超出时抛 ValueError。"""
        _validate_rgb(self.r, self.g, self.b)

    @property
    def brightness(self) -> float:
        """感知亮度 [0.0, 1.0]（ITU-R BT.601 加权）。"""
        return (0.299 * self.r + 0.587 * self.g + 0.114 * self.b) / 255.0

    def __str__(self) -> str:
        return f"RGB({self.r}, {self.g}, {self.b})"


# ═══════════════════════════════════════════════════════════
# TrueColor 值对象
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TrueColor:
    """TrueColor 24-bit 真彩色值对象。

    直接生成 ANSI 24-bit 转义序列（38;2 / 48;2），
    并支持降级到 256 色。

    Attributes:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。
    """
    r: int
    g: int
    b: int

    def __post_init__(self) -> None:
        """校验分量范围，超出时抛 ValueError。"""
        _validate_rgb(self.r, self.g, self.b)

    # ── ANSI 序列 ──

    def to_ansi_fg(self) -> str:
        """生成 TrueColor 前景色 ANSI 序列 ``\\033[38;2;{r};{g};{b}m``。

        Returns:
            ANSI 转义序列字符串。
        """
        return f"\033[38;2;{self.r};{self.g};{self.b}m"

    def to_ansi_bg(self) -> str:
        """生成 TrueColor 背景色 ANSI 序列 ``\\033[48;2;{r};{g};{b}m``。

        Returns:
            ANSI 转义序列字符串。
        """
        return f"\033[48;2;{self.r};{self.g};{self.b}m"

    # ── 降级 ──

    def to_256(self) -> int:
        """降级为最接近的 256 色号。

        使用欧氏距离在 xterm-256 调色板中查找最接近的颜色。

        Returns:
            最接近的 xterm-256 色号（0-255）。
        """
        return _find_closest_256(self.r, self.g, self.b)

    def to_256_color(self) -> Color256:
        """降级为 Color256 值对象。

        Returns:
            最接近的 Color256 实例。
        """
        return Color256(self.to_256())

    # ── 工厂方法 ──

    @classmethod
    def from_hex(cls, hex_color: str) -> TrueColor:
        """从十六进制颜色字符串创建 TrueColor。

        支持 ``#FF8800`` 和 ``ff8800`` 两种格式。

        Args:
            hex_color: 十六进制颜色字符串（6 位十六进制，可选 # 前缀）。

        Returns:
            TrueColor 实例。

        Raises:
            ValueError: 格式非法。
        """
        rgb = hex_to_rgb(hex_color)
        return cls(rgb.r, rgb.g, rgb.b)

    @classmethod
    def best_effort(cls, r: int, g: int, b: int,
                    supports_truecolor: bool = False) -> ColorValue:
        """根据终端能力自动选择最佳颜色类型。

        TrueColor 可用时返回 TrueColor，否则返回降级的 Color256。

        注意: 调用方应从 ``terminal.capabilities.supports_truecolor()``
        获取终端能力检测结果，并通过 ``supports_truecolor`` 参数传入。
        此设计保持 core 层零终端依赖。

        Args:
            r: 红色分量（0-255）。
            g: 绿色分量（0-255）。
            b: 蓝色分量（0-255）。
            supports_truecolor: 终端是否支持 TrueColor（调用方传入检测结果）。

        Returns:
            TrueColor 或 Color256，取决于终端能力。
        """
        if supports_truecolor:
            return cls(r, g, b)
        logger.debug(
            "TrueColor not supported, falling back to Color256 for "
            "RGB(%d, %d, %d)", r, g, b
        )
        return Color256.from_rgb(r, g, b)

    # ── 属性 ──

    @property
    def brightness(self) -> float:
        """感知亮度 [0.0, 1.0]（ITU-R BT.601 加权）。"""
        return (0.299 * self.r + 0.587 * self.g + 0.114 * self.b) / 255.0

    # ── 显示 ──

    def __str__(self) -> str:
        """返回 ANSI 前景色序列。"""
        return self.to_ansi_fg()

    def __repr__(self) -> str:
        return f"TrueColor({self.r}, {self.g}, {self.b})"


# ═══════════════════════════════════════════════════════════
# Color256 值对象
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Color256:
    """256 色值对象（冻结 dataclass）。

    包装 int [0, 255]，提供校验、RGB 转换、亮度计算、
    加减运算（带 clamp）等核心功能。

    Attributes:
        value: 256 色号（0-255）。
    """
    value: int

    def __post_init__(self) -> None:
        """校验色号范围，超出时抛 ValueError。"""
        if not (0 <= self.value <= 255):
            raise ValueError(
                f"Color256 value must be in [0, 255], got {self.value}"
            )

    # ── 工厂方法 ──

    @classmethod
    def from_rgb(cls, r: int, g: int, b: int) -> Color256:
        """从 RGB 分量创建最接近的 Color256。

        Args:
            r: 红色分量（0-255）。
            g: 绿色分量（0-255）。
            b: 蓝色分量（0-255）。

        Returns:
            最接近的 Color256 实例。
        """
        return cls(_find_closest_256(r, g, b))

    # ── 属性 ──

    @property
    def to_rgb(self) -> tuple[int, int, int]:
        """反查当前色号的 RGB 值。

        Returns:
            (r, g, b) 元组，各分量范围 [0, 255]。
        """
        return _XTERM_PALETTE[self.value]

    @property
    def brightness(self) -> float:
        """感知亮度 [0.0, 1.0]（ITU-R BT.601 加权）。

        基于当前色号反查 RGB 后计算。
        """
        r, g, b = _XTERM_PALETTE[self.value]
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0

    # ── 运算符 ──

    def __add__(self, other: int) -> Color256:
        """加偏移（clamp 到 [0, 255]）。"""
        if isinstance(other, int):
            return Color256(max(0, min(255, self.value + other)))
        return NotImplemented

    def __sub__(self, other: int) -> Color256:
        """减偏移（clamp 到 [0, 255]）。"""
        if isinstance(other, int):
            return Color256(max(0, min(255, self.value - other)))
        return NotImplemented

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Color256):
            return self.value == other.value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.value)

    # ── 显示 ──

    def __str__(self) -> str:
        """返回 ANSI 前景色序列 ``\\033[38;5;{value}m``。"""
        return f"\033[38;5;{self.value}m"

    def __repr__(self) -> str:
        return f"Color256({self.value})"


# ═══════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════


def _validate_rgb(r: int, g: int, b: int) -> None:
    """校验 RGB 分量范围 [0, 255]，超出时抛 ValueError。

    Args:
        r: 红色分量。
        g: 绿色分量。
        b: 蓝色分量。

    Raises:
        ValueError: 任一分量不在 [0, 255] 范围内。
    """
    if not (0 <= r <= 255):
        raise ValueError(f"r must be in [0, 255], got {r}")
    if not (0 <= g <= 255):
        raise ValueError(f"g must be in [0, 255], got {g}")
    if not (0 <= b <= 255):
        raise ValueError(f"b must be in [0, 255], got {b}")


def _find_closest_256(r: int, g: int, b: int) -> int:
    """在 xterm-256 调色板中查找最接近的色号。

    使用欧氏距离（平方）度量颜色差异，返回距离最小的色号。
    精确匹配时提前退出。

    Args:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。

    Returns:
        最接近的色号（0-255）。
    """
    best_idx: int = 0
    best_dist: int = 2**31 - 1

    for i, (cr, cg, cb) in enumerate(_XTERM_PALETTE):
        dr, dg, db = r - cr, g - cg, b - cb
        dist: int = dr * dr + dg * dg + db * db
        if dist < best_dist:
            best_dist = dist
            best_idx = i
            if best_dist == 0:  # 精确匹配，提前退出
                break

    return best_idx


# ═══════════════════════════════════════════════════════════
# 颜色转换纯函数
# ═══════════════════════════════════════════════════════════


def hex_to_rgb(hex_color: str) -> RGB:
    """解析十六进制颜色字符串为 RGB 值对象。

    支持 ``#FF8800`` 和 ``ff8800`` 两种格式。

    Args:
        hex_color: 十六进制颜色字符串（6 位十六进制，可选 # 前缀）。

    Returns:
        RGB 值对象。

    Raises:
        ValueError: 格式非法（非 6 位十六进制）。
    """
    cleaned = hex_color.lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(
            f"hex_color must be 6 hex digits, got '{cleaned}' "
            f"(from '{hex_color}')"
        )
    r = int(cleaned[0:2], 16)
    g = int(cleaned[2:4], 16)
    b = int(cleaned[4:6], 16)
    return RGB(r, g, b)


@lru_cache(maxsize=256)
def rgb_to_256(r: int, g: int, b: int) -> int:
    """将 RGB 分量映射到最接近的 xterm-256 色号。

    Args:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。

    Returns:
        最接近的色号（0-255）。
    """
    return _find_closest_256(r, g, b)


@lru_cache(maxsize=512)
def lerp_color(a: int, b: int, t: float) -> int:
    """两个 256 色号间线性插值。

    先将 a/b 反查为 RGB，在 RGB 空间线性插值，
    再映射回最接近的 256 色号。

    Args:
        a: 起始色号（0-255）。
        b: 结束色号（0-255）。
        t: 插值因子 [0.0, 1.0]，0.0→a，1.0→b。

    Returns:
        插值后的色号（0-255）。
    """
    if t <= 0.0:
        return a
    if t >= 1.0:
        return b

    ra, ga, ba = _XTERM_PALETTE[a]
    rb, gb, bb = _XTERM_PALETTE[b]

    r = round(ra + t * (rb - ra))
    g = round(ga + t * (gb - ga))
    b = round(ba + t * (bb - ba))

    return _find_closest_256(
        max(0, min(255, r)),
        max(0, min(255, g)),
        max(0, min(255, b)),
    )


# ═══════════════════════════════════════════════════════════
# GradientDescriptor 值对象
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class GradientDescriptor:
    """渐变描述值对象。

    定义渐变的起始/结束色号、步数和动效类型，
    通过 resolve() / resolve_with_effect() 生成具体色号列表。

    Attributes:
        start_color: 起始 256 色号（0-255）。
        end_color: 结束 256 色号（0-255）。
        steps: 渐变步数，默认 8。
        effect: 动效类型，"none"（无）/ "wave"（波动）/ "shimmer"（流光）。
    """
    start_color: int
    end_color: int
    steps: int = 8
    effect: str = "none"

    __effect_options: tuple[str, ...] = ("none", "wave", "shimmer")

    def __post_init__(self) -> None:
        """校验字段范围。"""
        if not (0 <= self.start_color <= 255):
            raise ValueError(
                f"start_color must be in [0, 255], "
                f"got {self.start_color}"
            )
        if not (0 <= self.end_color <= 255):
            raise ValueError(
                f"end_color must be in [0, 255], "
                f"got {self.end_color}"
            )
        if self.steps < 1:
            raise ValueError(
                f"steps must be >= 1, got {self.steps}"
            )
        if self.effect not in self.__effect_options:
            raise ValueError(
                f"effect must be one of {self.__effect_options}, "
                f"got '{self.effect}'"
            )

    def resolve(self) -> list[int]:
        """生成色号列表。

        调用 ``gradient_range()`` 生成 steps 个均匀分布的色号。
        延迟导入避免模块加载循环。

        Returns:
            steps 个色号的列表（steps=1 时返回 [start_color]）。
        """
        from .gradient import gradient_range
        return gradient_range(self.start_color, self.end_color, self.steps)

    def resolve_with_effect(self, frame: int) -> list[int]:
        """生成带动效的色号列表。

        先调用 resolve() 生成基础渐变，再根据 effect 类型施加动效。
        动效函数延迟导入。

        Args:
            frame: 当前帧号（动效推进使用），≤ 0 时跳过动效。

        Returns:
            动效处理后的色号列表，长度与 steps 一致。
        """
        if self.effect == "none" or frame <= 0:
            return self.resolve()

        colors = self.resolve()

        if self.effect == "wave":
            from .effects import apply_wave
            return apply_wave(colors, frame)

        if self.effect == "shimmer":
            from .effects import shimmer_apply
            return shimmer_apply(colors, frame)

        return colors


# ═══════════════════════════════════════════════════════════
# ColorValue 联合类型与转换函数
# ═══════════════════════════════════════════════════════════

#: 颜色值联合类型，统一 Color256 / TrueColor / int（256 色号，向后兼容）。
ColorValue = Union[Color256, TrueColor, int]


def to_ansi_fg(color: ColorValue) -> str:
    """统一生成前景色 ANSI 转义序列。

    - Color256 → ``\\033[38;5;{value}m``
    - TrueColor → ``\\033[38;2;{r};{g};{b}m``
    - int → ``\\033[38;5;{value}m``

    Args:
        color: 颜色值（Color256 / TrueColor / int）。

    Returns:
        ANSI 前景色转义序列。
    """
    if isinstance(color, TrueColor):
        return color.to_ansi_fg()
    if isinstance(color, Color256):
        return f"\033[38;5;{color.value}m"
    # int: 256 色号
    return f"\033[38;5;{color}m"


def to_ansi_bg(color: ColorValue) -> str:
    """统一生成背景色 ANSI 转义序列。

    - Color256 → ``\\033[48;5;{value}m``
    - TrueColor → ``\\033[48;2;{r};{g};{b}m``
    - int → ``\\033[48;5;{value}m``

    Args:
        color: 颜色值（Color256 / TrueColor / int）。

    Returns:
        ANSI 背景色转义序列。
    """
    if isinstance(color, TrueColor):
        return color.to_ansi_bg()
    if isinstance(color, Color256):
        return f"\033[48;5;{color.value}m"
    # int: 256 色号
    return f"\033[48;5;{color}m"


def to_256(color: ColorValue) -> int:
    """统一降级为 256 色号。

    - Color256 → 直接返回 value
    - TrueColor → 查找最接近的 256 色号
    - int → 原样返回

    Args:
        color: 颜色值（Color256 / TrueColor / int）。

    Returns:
        256 色号（0-255）。
    """
    if isinstance(color, TrueColor):
        return color.to_256()
    if isinstance(color, Color256):
        return color.value
    # int: 原样返回
    return color


def auto_color(r: int, g: int, b: int) -> ColorValue:
    """根据终端能力自动选择最佳颜色类型。

    TrueColor 可用时返回 TrueColor，否则返回 Color256。
    内部调用 ``supports_truecolor()`` 进行终端能力检测。

    Args:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。

    Returns:
        TrueColor 或 Color256，取决于终端能力。
    """
    return TrueColor.best_effort(r, g, b)


# ═══════════════════════════════════════════════════════════
# 模块导出
# ═══════════════════════════════════════════════════════════

__all__ = [
    # 值对象
    "Color256",
    "RGB",
    "TrueColor",
    "GradientDescriptor",
    # 联合类型
    "ColorValue",
    # 转换函数
    "hex_to_rgb",
    "rgb_to_256",
    "lerp_color",
    "to_ansi_fg",
    "to_ansi_bg",
    "to_256",
    "auto_color",
]
