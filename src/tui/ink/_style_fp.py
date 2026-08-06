"""ink/_style_fp — 稳定可哈希样式指纹（差异封装）。

为 ``layout._wrap_cache`` 等需要稳定缓存键的场景提供 Style 值指纹，
替代基于 ``id()`` 的对象身份（BUG-T1：id() 在对象 GC 后可能复用，
导致错误缓存命中/未命中）。

指纹规则（值相等 → 指纹相等；不依赖对象生命周期）：
  - fg/bg 为 None → -1
  - fg/bg 为 int（256 色号）→ 保持 int
  - fg/bg 为 TrueColor → (r, g, b) 三元组
  指纹 = (fg_comp, bg_comp, bold, italic, dim, underline, strikethrough, inverse)

设计模式：享元（Flyweight）— 将重复的 Style 指纹复用为稳定键。

注意：Style 新增字段时需同步更新本指纹函数（保持覆盖全部样式属性）。
"""

from __future__ import annotations

from typing import Tuple, Union

from src.tui.core.color import TrueColor
from src.tui.core.style import Style

#: 颜色分量指纹类型：-1（None）/ int（256 色号）/ (r, g, b) 三元组
_ColorFp = Union[int, Tuple[int, int, int]]


def _color_component(color) -> _ColorFp:
    """将 fg/bg 颜色值归一化为稳定可哈希分量。

    Args:
        color: Style.fg / Style.bg 值（None / int / TrueColor）。

    Returns:
        -1（None）、int（256 色号）或 (r, g, b) 三元组。

    ★ P3-12（review 方向）：非 int/TrueColor 值（如颜色名字符串
    ``"red"``）修复前 ``int(color)`` 抛 ValueError——指纹计算崩溃导致布局
    wrap 缓存异常（style_fingerprint 为热路径调用）。现返回哨兵 ``-2``
    （未知颜色；可哈希稳定，仅指纹区分，不参与真实渲染——渲染层颜色解析
    由 _parse_color/样式系统负责，指纹只要求值稳定可哈希）。
    """
    if color is None:
        return -1
    if isinstance(color, TrueColor):
        return (color.r, color.g, color.b)
    if isinstance(color, int):
        return color
    return -2


def style_fingerprint(style: Style) -> tuple:
    """返回 Style 的稳定可哈希指纹（值驱动，不依赖 id()/对象生命周期）。

    Args:
        style: 要生成指纹的 Style 实例。

    Returns:
        可哈希元组；相同样式值 → 相同指纹，不同样式值 → 不同指纹。
    """
    return (
        _color_component(style.fg),
        _color_component(style.bg),
        style.bold,
        style.italic,
        style.dim,
        style.underline,
        # ★ 兼容 renderer/ansi/style.py 的 Style（无 strikethrough/inverse 字段
        #   ——旧字段子集）。getattr 安全读取：缺字段视为 False（指纹仅区分
        #   样式值，不涉及新字段的 renderer Style 指纹等价）。
        getattr(style, "strikethrough", False),
        getattr(style, "inverse", False),
    )


__all__ = ["style_fingerprint"]
