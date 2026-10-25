"""Color256 值对象与颜色转换模块（Layer 0 Core）。

颜色输出恒为 24-bit 真彩色（2026-10-25 用户需求：TUI 颜色全部真彩色，
删除原 256 色降级路径）：

  - Color256: xterm-256 调色板**索引**值对象（0-255），带校验/转换/运算；
    索引只在内部参与调色板运算，输出时一律反查为精确 RGB。
  - RGB: RGB 颜色值对象
  - TrueColor: 24-bit 真彩色值对象
  - ansi_fg / ansi_bg: TUI 颜色统一输出口——调色板索引按 xterm-256 调色板
    反查精确 RGB，**恒输出** ``38;2``/``48;2``；``NO_COLOR`` 存在时输出空串
    （禁用颜色）。``to_ansi_fg``/``to_ansi_bg`` 为等价的纯编码函数。
  - hex_to_rgb / rgb_to_256 / lerp_color: 颜色转换纯函数（@lru_cache 缓存）
  - GradientDescriptor: 渐变描述值对象（字段 + 校验；resolve 已随渐变模块删除）

设计原则：
  - 纯函数/值对象：无副作用，不可变
  - 缓存热点：rgb_to_256 / lerp_color 使用 @lru_cache 减少重复计算
  - 颜色开关进程级缓存：``NO_COLOR`` 判定一次后固定
    （``_reset_color_disabled_cache`` 仅供测试重置）
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Union
from src._compat import dataclass


# ═══════════════════════════════════════════════════════════
# 颜色开关（NO_COLOR 规范）
# ═══════════════════════════════════════════════════════════
# 2026-10-25（用户需求：TUI 颜色全部真彩色，删除原来的 256 色）：
# 终端真彩能力检测（COLORTERM / TERM direct / 降级 256 色）已删除——颜色
# 输出恒为 24-bit 真彩。仅保留 ``NO_COLOR`` 规范（存在即禁用颜色，输出空串）。
# 结果模块级缓存（判定一次，进程内不变）。

_colors_disabled_result: bool | None = None


def colors_disabled() -> bool:
    """``NO_COLOR`` 规范：环境变量存在（含空串）即禁用颜色（进程级缓存）。

    Returns:
        True 表示不输出任何颜色（``ansi_fg``/``ansi_bg`` 返回空串）。
    """
    global _colors_disabled_result
    if _colors_disabled_result is None:
        _colors_disabled_result = "NO_COLOR" in os.environ
    return _colors_disabled_result


def _reset_color_disabled_cache() -> None:
    """清空 ``NO_COLOR`` 判定缓存（测试用）。"""
    global _colors_disabled_result
    _colors_disabled_result = None


# ── xterm 调色板（2026-08-05 模块边界优化：迁至 core/_palette.py）──
# 只 re-export 被本模块使用的符号（_build_xterm_palette 为 _palette 内部
# 构建函数，本模块不消费——清理死 import）。
from src.tui.core._palette import (
    _XTERM_PALETTE,
    _find_closest_256,
)


# ═══════════════════════════════════════════════════════════
# RGB 值对象
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class RGB:
    """RGB 颜色值对象。

    三个分量 r/g/b 各为 [0, 255] 范围的整数。
    冻结 + slots，不可变。

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


@dataclass(frozen=True, slots=True)
class TrueColor:
    """TrueColor 24-bit 真彩色值对象。

    直接生成 ANSI 24-bit 转义序列（38;2 / 48;2）；``to_256`` 提供调色板
    索引转换（仅供内部索引运算，颜色输出恒为真彩）。

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

    # ── 调色板索引转换（内部运算用；输出恒真彩）──

    def to_256(self) -> int:
        """转换为最接近的 xterm-256 调色板索引。

        使用欧氏距离在 xterm-256 调色板中查找最接近的颜色。

        Returns:
            最接近的调色板索引（0-255）。
        """
        return _find_closest_256(self.r, self.g, self.b)

    def to_256_color(self) -> Color256:
        """转换为 Color256 值对象（调色板索引）。

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
    def best_effort(cls, r: int, g: int, b: int) -> TrueColor:
        """创建 TrueColor（颜色输出恒为 24-bit 真彩，不再按终端能力降级）。

        Args:
            r: 红色分量（0-255）。
            g: 绿色分量（0-255）。
            b: 蓝色分量（0-255）。

        Returns:
            TrueColor 实例。
        """
        return cls(r, g, b)

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


@dataclass(frozen=True, slots=True)
class Color256:
    """xterm-256 调色板索引值对象（冻结 slots dataclass）。

    包装 int [0, 255]，提供校验、RGB 转换、亮度计算、
    加减运算（带 clamp）等核心功能。索引仅参与内部运算，输出恒为 24-bit
    真彩（反查调色板精确 RGB）。

    Attributes:
        value: 调色板索引（0-255）。
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
        """反查当前索引的 RGB 值。

        Returns:
            (r, g, b) 元组，各分量范围 [0, 255]。
        """
        return _XTERM_PALETTE[self.value]

    @property
    def brightness(self) -> float:
        """感知亮度 [0.0, 1.0]（ITU-R BT.601 加权）。

        基于当前索引反查 RGB 后计算。
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
        """返回 ANSI 前景色序列（恒 24-bit 真彩，索引反查调色板 RGB）。"""
        return to_ansi_fg(self)

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
    # ★ P3（review 2026-08-22）：非十六进制字符（如 ``#gg8800``）int(...,16)
    #   抛裸 ValueError——转抛带 hex_color 上下文的 ValueError。
    try:
        r = int(cleaned[0:2], 16)
        g = int(cleaned[2:4], 16)
        b = int(cleaned[4:6], 16)
    except ValueError:
        raise ValueError(
            f"hex_color must be 6 hex digits, got '{cleaned}' "
            f"(from '{hex_color}')"
        )
    return RGB(r, g, b)


@lru_cache(maxsize=256)
def rgb_to_256(r: int, g: int, b: int) -> int:
    """将 RGB 分量映射到最接近的 xterm-256 调色板索引。

    Args:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。

    Returns:
        最接近的调色板索引（0-255）。
    """
    return _find_closest_256(r, g, b)


@lru_cache(maxsize=512)
def lerp_color(a: int, b: int, t: float) -> int:
    """两个调色板索引间线性插值（内部运算；输出时反查精确 RGB 发真彩）。

    先将 a/b 反查为 RGB，在 RGB 空间线性插值，
    再映射回最接近的调色板索引。

    Args:
        a: 起始索引（0-255）。
        b: 结束索引（0-255）。
        t: 插值因子 [0.0, 1.0]，0.0→a，1.0→b。

    Returns:
        插值后的色号（0-255）。
    """
    # ★ BUG-51（review 方向）：非有限 t（NaN/inf）返回 a（与 format_* 的
    #   isfinite 防护一致）——修复前 NaN 走完比较后 ``round(NaN)`` 抛
    #   ValueError 中断渲染（时间基动效的 t 来自正弦插值，理论不产生 NaN，
    #   防御外部调用方传入异常 t）。
    # ★ P1-1（最关键）：a/b 超出 [0, 255]（如负值）时 ``_XTERM_PALETTE[a]``
    #   抛 IndexError（无范围校验）——入口显式校验并抛 ValueError，
    #   与 Color256 构造校验语义一致（明确报错而非越界索引）。
    if not (0 <= a <= 255) or not (0 <= b <= 255):
        raise ValueError(
            f"lerp_color a/b must be in [0, 255], got a={a}, b={b}"
        )
    # ★ P3（review 2026-08-22）：非有限 t 检查移到范围校验**之后**——修复前
    #   ``if not math.isfinite(t): return a`` 位于范围校验前，越界 a + NaN t
    #   时返回越界色号（此时 a 已保证合法）。
    if not math.isfinite(t):
        return a
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


@dataclass(frozen=True, slots=True)
class GradientDescriptor:
    """渐变描述值对象。

    定义渐变的起始/结束色号、步数和动效类型。

    注：resolve()/resolve_with_effect() 已随 gradient/effects 模块删除
    （2026-07-31 幽灵导入清理），本类保留为纯值对象（字段 + 校验）。

    Attributes:
        start_color: 起始调色板索引（0-255）。
        end_color: 结束调色板索引（0-255）。
        steps: 渐变步数，默认 8。
        effect: 动效类型，"none"（无）/ "wave"（波动）/ "shimmer"（流光）。
    """
    start_color: int
    end_color: int
    steps: int = 8
    effect: str = "none"

    # 类级常量（合法动效选项集）。**不带类型注解**的纯类变量赋值：dataclass
    # 只处理 __annotations__ 中的名字，无注解类变量既不会被当作实例字段
    # （repr 不显示 _GradientDescriptor__effect_options=...），也不会被
    # _compat 低版本 slots 模拟加入 __slots__（修复前带注解赋值在 Python<3.10
    # 下被 slots 模拟误纳入 __slots__ → 实例访问 slot 未初始化 AttributeError）。
    __effect_options = ("none", "wave", "shimmer")

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


# ═══════════════════════════════════════════════════════════
# ColorValue 联合类型与转换函数
# ═══════════════════════════════════════════════════════════

#: 颜色值联合类型，统一 Color256 / TrueColor / int（xterm-256 调色板索引）。
ColorValue = Union[Color256, TrueColor, int]


def to_ansi_fg(color: ColorValue) -> str:
    """统一生成前景色 ANSI 转义序列（恒 24-bit 真彩）。

    - Color256 / int（xterm-256 调色板索引）→ 反查调色板精确 RGB →
      ``\\033[38;2;{r};{g};{b}m``
    - TrueColor → ``\\033[38;2;{r};{g};{b}m``
    - 非法输入（bool / None / 非 int）→ 空串
    - ``NO_COLOR`` 存在 → 空串（禁用颜色）

    Args:
        color: 颜色值（Color256 / TrueColor / int 调色板索引）。

    Returns:
        ANSI 前景色转义序列。
    """
    if colors_disabled():
        return ""
    if isinstance(color, TrueColor):
        return color.to_ansi_fg()
    if isinstance(color, Color256):
        value = color.value
    elif isinstance(color, bool) or not isinstance(color, int):
        return ""
    else:
        # int: xterm-256 调色板索引（P3-17：钳制到 [0, 255]，容忍非法输入
        # 不生成越界 ANSI 序列）
        value = max(0, min(255, color))
    r, g, b = _XTERM_PALETTE[value]
    return f"\033[38;2;{r};{g};{b}m"


def to_ansi_bg(color: ColorValue) -> str:
    """统一生成背景色 ANSI 转义序列（恒 24-bit 真彩，语义同 ``to_ansi_fg``）。

    Args:
        color: 颜色值（Color256 / TrueColor / int 调色板索引）。

    Returns:
        ANSI 背景色转义序列。
    """
    if colors_disabled():
        return ""
    if isinstance(color, TrueColor):
        return color.to_ansi_bg()
    if isinstance(color, Color256):
        value = color.value
    elif isinstance(color, bool) or not isinstance(color, int):
        return ""
    else:
        value = max(0, min(255, color))
    r, g, b = _XTERM_PALETTE[value]
    return f"\033[48;2;{r};{g};{b}m"


def ansi_fg(color: ColorValue) -> str:
    """能力无关的前景色 ANSI 序列（TUI 颜色统一出口，恒 24-bit 真彩）。

    - ``TrueColor`` → ``\\033[38;2;{r};{g};{b}m``。
    - ``int`` / ``Color256``（xterm-256 调色板索引）→ 反查精确 RGB 输出
      ``\\033[38;2;{r};{g};{b}m``——索引本身就是调色板位置，反查得到精确
      RGB，因此真彩化不改变既有配色观感，只是把编码从查表色号变为 24-bit；
      0-15 系统色使用 xterm 标准值。
    - 非法输入（bool / None / 非 int）→ 空串。
    - ``NO_COLOR`` 存在 → 空串（禁用颜色）。

    Args:
        color: 颜色值（Color256 / TrueColor / int）。

    Returns:
        ANSI 前景色转义序列。
    """
    return to_ansi_fg(color)


def ansi_bg(color: ColorValue) -> str:
    """能力无关的背景色 ANSI 序列（TUI 颜色统一出口，语义同 ``ansi_fg``）。

    Args:
        color: 颜色值（Color256 / TrueColor / int）。

    Returns:
        ANSI 背景色转义序列。
    """
    return to_ansi_bg(color)


def to_256(color: ColorValue) -> int:
    """统一转换为 xterm-256 调色板索引（内部运算用；输出恒真彩）。

    - Color256 → 直接返回 value
    - TrueColor → 查找最接近的调色板索引
    - int → 原样返回

    Args:
        color: 颜色值（Color256 / TrueColor / int）。

    Returns:
        调色板索引（0-255）。
    """
    if isinstance(color, TrueColor):
        return color.to_256()
    if isinstance(color, Color256):
        return color.value
    if not isinstance(color, int) or isinstance(color, bool):
        # ★ 修复（P2-1）：非 int 输入（None/str 等）返回 0——修复前
        #   max(0, min(255, color)) 抛 TypeError（渲染中断）；bool 是 int
        #   子类（True→1 / False→0），显式排除（与 to_ansi_fg/to_ansi_bg
        #   对齐）。
        return 0
    # int: 调色板索引（P3-17：钳制到 [0, 255]，容忍非法输入返回合法索引）
    return max(0, min(255, color))


def auto_color(r: int, g: int, b: int) -> TrueColor:
    """创建真彩色值对象（颜色输出恒真彩，不再按终端能力降级）。

    Args:
        r: 红色分量（0-255）。
        g: 绿色分量（0-255）。
        b: 蓝色分量（0-255）。

    Returns:
        TrueColor 实例。
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
    "ansi_fg",
    "ansi_bg",
    "to_256",
    "auto_color",
    # 颜色开关（NO_COLOR 规范）
    "colors_disabled",
    "_reset_color_disabled_cache",
]
