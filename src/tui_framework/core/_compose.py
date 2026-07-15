"""EffectRegistry 合成器与效果包装 — 效果注册表及霓虹/打字机等便捷效果。

包含：
  - EffectRegistry：命名效果注册表，统一发现和组合动画效果
  - neon_* 系列：霓虹边框效果
  - typewriter_* 系列：打字机光标闪烁效果
  - sine_easing：正弦缓动函数（统一入口）

设计原则：
  - EffectRegistry 线程安全，所有操作为只读字典访问 + 纯函数
  - 效果函数签名统一为 ``effect_fn(frame: int, **params) -> list[int] | str``
"""

from __future__ import annotations

import math
from typing import Any, Callable, ClassVar

from ._wave import sine_color, apply_wave
from ._sparkle import shimmer_apply
from ._train import (
    rainbow_color,
    build_aurora_gradient,
    pulse_train,
    apply_heat_wave,
)


# ═══════════════════════════════════════════════════════════
# 缓动函数（统一入口）
# ═══════════════════════════════════════════════════════════


def sine_easing(t: float) -> float:
    """正弦平滑缓动 [0,1] → [0,1]，两端减速。

    与 ``sine_breath_t`` 同族但语义不同：
    后者是绝对帧号的归一化值，前者是纯缓动因子。
    从 ``transitions._easing_smooth`` 迁移，统一缓动函数入口。

    Args:
        t: 归一化时间 [0.0, 1.0]。

    Returns:
        缓动后的值 [0.0, 1.0]，两端导数趋近0。
    """
    return (math.sin(t * math.pi - math.pi / 2.0) + 1.0) / 2.0


# ═══════════════════════════════════════════════════════════
# 霓虹边框效果（neon_border）
# ═══════════════════════════════════════════════════════════


def neon_color(frame: int, base_color: int = 51, spread: int = 3) -> int:
    """霓虹灯管色号摆动 — 模拟霓虹灯管的不稳定发光。

    使用正弦波在 base_color ± spread 范围内漂移，
    每帧颜色微调产生"闪烁"感。

    Args:
        frame: 当前帧号。
        base_color: 基准色号（默认 51=霓虹青色）。
        spread: 摆动幅度（色号范围 ±spread）。

    Returns:
        xterm-256 色号（0-255）。
    """
    offset = round((math.sin(frame * 0.8) + math.sin(frame * 1.3) * 0.5) / 1.5 * spread)
    return max(0, min(255, base_color + offset))


def build_neon_border_ansi(
    text: str, frame: int, base_color: int = 51, width: int | None = None,
    narrow: bool = False,
) -> str:
    """构建霓虹边框 ANSI 字符串。

    对文本四边包裹霓虹色边框，每帧颜色微调产生"闪烁"感。
    窄屏时降级为单色边框（使用现有呼吸效果替代）。

    边框格式::

        ┌──────────┐
        │  text    │
        └──────────┘

    Args:
        text: 要包裹的文本内容（支持多行）。
        frame: 当前帧号。
        base_color: 霓虹基准色号（默认 51=青色）。
        width: 边框宽度，None 时自动取最长行宽。

    Returns:
        带霓虹边框的 ANSI 字符串。
    """
    from .ansi_utils import visual_width as _vw

    lines = text.split("\n")
    if width is None:
        width = max((_vw(line) for line in lines), default=0)
    width = max(width, 1)

    # 窄屏降级：使用呼吸前景色替代霓虹闪烁
    if narrow:
        color = sine_color(frame, base_color, min(255, base_color + 10), 12)
        color_ansi = f"\033[38;5;{color}m"
        reset = "\033[0m"
        h_line = "\u2500" * width
        top = f"{color_ansi}\u250c{h_line}\u2510{reset}"
        body = "\n".join(
            f"{color_ansi}\u2502{line}{' ' * (width - _vw(line))}\u2502{reset}"
            for line in lines
        )
        bottom = f"{color_ansi}\u2514{h_line}\u2518{reset}"
        return f"{top}\n{body}\n{bottom}"

    # 全功能霓虹边框：每帧颜色微调
    top_color = neon_color(frame, base_color, spread=3)
    mid_color = neon_color(frame + 2, base_color, spread=2)
    bottom_color = neon_color(frame + 4, base_color, spread=3)

    top_ansi = f"\033[38;5;{top_color}m"
    mid_ansi = f"\033[38;5;{mid_color}m"
    bottom_ansi = f"\033[38;5;{bottom_color}m"
    reset = "\033[0m"

    h_line = "\u2500" * width
    top = f"{top_ansi}\u250c{h_line}\u2510{reset}"
    body = "\n".join(
        f"{mid_ansi}\u2502{line}{' ' * (width - _vw(line))}\u2502{reset}"
        for line in lines
    )
    bottom = f"{bottom_ansi}\u2514{h_line}\u2518{reset}"
    return f"{top}\n{body}\n{bottom}"


# ═══════════════════════════════════════════════════════════
# 打字机光标闪烁效果（typewriter_cursor）
# ═══════════════════════════════════════════════════════════


def typewriter_cursor(frame: int, period: int = 2) -> str:
    """打字机光标闪烁 — 返回可见光标或空白，交替闪烁。

    偶帧返回 ``▌``（左半块），奇帧返回空格，
    模拟打字机/终端输入光标的闪烁效果。

    Args:
        frame: 当前帧号。
        period: 闪烁周期帧数（默认 2 帧：显示/隐藏各 1 帧）。

    Returns:
        ``"▌"`` 或 ``" "``。
    """
    return "\u258c" if (frame // (period // 2)) % 2 == 0 else " "


def build_typewriter_ansi(
    text: str,
    reveal_count: int,
    frame: int,
    style: str | None = None,
) -> str:
    """构建打字机效果 ANSI 字符串 — 已揭示文本 + 闪烁光标。

    逐字符揭示文本内容，末尾追加闪烁光标，
    模拟打字机逐字输出的视觉效果。

    格式::

        {styled revealed_text}{cursor_char}

    Args:
        text: 完整文本内容。
        reveal_count: 已揭示的字符数（0 到 len(text)）。
        frame: 当前帧号（控制光标闪烁）。
        style: 可选样式，``"dim"`` 表示未揭示部分灰色显示。

    Returns:
        带打字机效果的 ANSI 字符串。
    """
    revealed = text[:reveal_count]
    cursor = typewriter_cursor(frame)

    if style == "dim" and reveal_count < len(text):
        hidden = text[reveal_count:]
        return f"{revealed}{cursor}\033[2m{hidden}\033[0m"
    else:
        return f"{revealed}{cursor}"


# ═══════════════════════════════════════════════════════════
# EffectRegistry — 效果注册表
# ═══════════════════════════════════════════════════════════


class EffectRegistry:
    """命名效果注册表 — 统一发现和组合动画效果。

    模式参考 ``BreathPalette`` / ``StyleSheet``，但注册效果函数而非颜色。
    模块加载时自动注册所有预定义效果。

    每个注册项为 ``(effect_fn, metadata)`` 元组，
    其中 metadata 包含 description、params、category。

    效果函数签名统一为 ``effect_fn(frame: int, **params) -> list[int] | str``。
    — 返回色号列表（供渐变/分隔线使用）或 ANSI 字符串（供直接输出）。

    线程安全：所有操作为只读字典访问 + 纯函数。
    """

    _registry: ClassVar[dict[str, tuple[Callable, dict]]] = {}

    @classmethod
    def register(cls, name: str, effect_fn: Callable, **metadata) -> None:
        """注册命名效果。

        Args:
            name: 效果名称（如 "aurora", "rainbow"）。
            effect_fn: 效果函数，签名 ``effect_fn(frame: int, **params)``。
            **metadata: 元数据关键词（description, params, category 等）。
        """
        cls._registry[name] = (effect_fn, metadata)

    @classmethod
    def get(cls, name: str) -> Callable | None:
        """获取命名效果函数。不存在时返回 None。

        Args:
            name: 效果名称。

        Returns:
            效果函数，不存在时返回 None。
        """
        entry = cls._registry.get(name)
        if entry is not None:
            return entry[0]
        return None

    @classmethod
    def has(cls, name: str) -> bool:
        """检查效果是否已注册。

        Args:
            name: 效果名称。

        Returns:
            是否已注册。
        """
        return name in cls._registry

    @classmethod
    def list(cls) -> list[tuple[str, dict]]:
        """获取所有已注册效果的 (名称, metadata) 列表。

        Returns:
            (名称, metadata) 列表。
        """
        return [(name, meta) for name, (_, meta) in cls._registry.items()]

    @classmethod
    def compose(cls, names: list[str], frame: int, **kwargs) -> list[int]:
        """将多个命名效果按帧号混合输出到单色号序列。

        逐个调用每个效果函数，用平均权重混合输出。
        仅适用于返回 ``list[int]`` 色号列表的效果函数。

        Args:
            names: 效果名称列表（如 ["aurora", "rainbow"]）。
            frame: 当前帧号。
            **kwargs: 传递给各效果函数的附加参数。

        Returns:
            混合后的色号列表。无注册效果时返回空列表。

        Raises:
            ValueError: 某个命名效果未注册。
        """
        results: list[list[int]] = []
        for name in names:
            fn = cls.get(name)
            if fn is None:
                raise ValueError(f"未注册的效果: {name}")
            result = fn(frame, **kwargs)
            if isinstance(result, list):
                results.append(result)
        if not results:
            return []

        min_len = min(len(r) for r in results)
        return [
            max(0, min(255, round(sum(r[i] for r in results) / len(results))))
            for i in range(min_len)
        ]

    @classmethod
    def clear(cls) -> None:
        """清空注册表（供测试使用）。"""
        cls._registry.clear()

    @classmethod
    def all_names(cls) -> list[str]:
        """获取所有已注册的效果名称列表。

        Returns:
            效果名称列表。
        """
        return list(cls._registry.keys())


# ═══════════════════════════════════════════════════════════
# 预注册默认效果
# ═══════════════════════════════════════════════════════════


def _register_default_effects() -> None:
    """注册默认效果到 EffectRegistry。模块加载时自动调用。"""

    def _rainbow_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 8)
        return [rainbow_color(frame, i, length) for i in range(length)]

    def _aurora_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        colors = kwargs.get("colors")
        return build_aurora_gradient(length, frame, colors)

    def _pulse_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        color_low = kwargs.get("color_low", 239)
        color_high = kwargs.get("color_high", 45)
        return [pulse_train(frame, i, length, color_low, color_high)
                for i in range(length)]

    def _wave_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        start = kwargs.get("start_color", 45)
        end = kwargs.get("end_color", 237)
        from .gradient import gradient_range
        colors = gradient_range(start, end, length)
        amplitude = kwargs.get("amplitude", 2.0)
        return apply_wave(colors, frame, amplitude=amplitude)

    def _shimmer_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        start = kwargs.get("start_color", 45)
        end = kwargs.get("end_color", 237)
        from .gradient import gradient_range
        colors = gradient_range(start, end, length)
        return shimmer_apply(colors, frame)

    def _heat_wave_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        start = kwargs.get("start_color", 45)
        end = kwargs.get("end_color", 237)
        amplitude = kwargs.get("amplitude", 5.0)
        from .gradient import gradient_range
        colors = gradient_range(start, end, length)
        return [apply_heat_wave(c, i, frame, amplitude) for i, c in enumerate(colors)]

    EffectRegistry.register("rainbow", _rainbow_effect,
                            description="彩虹渐变效果",
                            params={"length": "色号数量"},
                            category="gradient")
    EffectRegistry.register("aurora", _aurora_effect,
                            description="极光飘动效果",
                            params={"length": "色号数量", "colors": "可选基础调色板"},
                            category="gradient")
    EffectRegistry.register("pulse", _pulse_effect,
                            description="脉冲列车效果",
                            params={"length": "宽度", "color_low": "基础色号", "color_high": "脉冲色号"},
                            category="gradient")
    EffectRegistry.register("wave", _wave_effect,
                            description="波动渐变效果",
                            params={"length": "宽度", "start_color": "起始色号", "end_color": "结束色号", "amplitude": "波动幅度"},
                            category="gradient")
    EffectRegistry.register("shimmer", _shimmer_effect,
                            description="流光扫光效果",
                            params={"length": "宽度", "start_color": "起始色号", "end_color": "结束色号"},
                            category="gradient")
    EffectRegistry.register("heat_wave", _heat_wave_effect,
                            description="热浪扭曲效果",
                            params={"length": "宽度", "start_color": "起始色号", "end_color": "结束色号", "amplitude": "热浪幅度"},
                            category="gradient")
    EffectRegistry.register("sparkle", None,
                            description="闪烁高亮效果",
                            params={"base_color": "基准色号", "period": "闪烁周期"},
                            category="ansi")
    EffectRegistry.register("glow", None,
                            description="辉光呼吸效果",
                            params={"base_color": "基准色号", "period": "呼吸周期"},
                            category="ansi")

    def _neon_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 12)
        base_color = kwargs.get("base_color", 51)
        spread = kwargs.get("spread", 3)
        return [neon_color(frame + i, base_color, spread) for i in range(length)]

    EffectRegistry.register("neon", _neon_effect,
                            description="霓虹边框效果",
                            params={"length": "色号数量", "base_color": "霓虹基准色号", "spread": "摆动幅度"},
                            category="gradient")

    def _typewriter_effect(frame: int, **kwargs: Any) -> list[int]:
        length = kwargs.get("length", 8)
        base_color = kwargs.get("base_color", 45)
        period = kwargs.get("period", 2)
        visible = (frame // (period // 2)) % 2 == 0
        fill = base_color if visible else 237
        return [fill] * length

    EffectRegistry.register("typewriter", _typewriter_effect,
                            description="打字机光标闪烁效果",
                            params={"length": "色号数量", "base_color": "基准色号", "period": "闪烁周期"},
                            category="gradient")


_register_default_effects()


__all__ = [
    "sine_easing",
    "neon_color", "build_neon_border_ansi",
    "typewriter_cursor", "build_typewriter_ansi",
    "EffectRegistry",
]
