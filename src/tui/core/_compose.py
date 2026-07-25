"""EffectRegistry 合成器与效果包装 — 从 effects.py 拆分。

包含：EffectRegistry 命名效果注册表、预注册闭包包装器、模块加载时自动注册。
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable, ClassVar

from ._wave import apply_wave, bounce_frame_color, sine_color, sine_easing
from ._sparkle import sparkle_color
from ._train import (
    apply_heat_wave, build_aurora_gradient, heat_wave_offset,
    neon_color, pulse_train, rainbow_color, shimmer_apply,
)


class EffectRegistry:
    """命名效果注册表 — 统一发现和组合动画效果。

    模式参考 BreathPalette / StyleSheet，但注册效果函数而非颜色。
    模块加载时自动注册所有预定义效果。

    每个注册项为 (effect_fn, metadata) 元组，
    其中 metadata 包含 description、params、category。

    效果函数签名统一为 effect_fn(frame: int, **params) -> list[int] | str。
    — 返回色号列表（供渐变/分隔线使用）或 ANSI 字符串（供直接输出）。

    线程安全：所有操作为只读字典访问 + 纯函数。
    """

    _registry: ClassVar[dict[str, tuple[Callable, dict]]] = {}

    @classmethod
    def register(cls, name: str, effect_fn: Callable, **metadata) -> None:
        cls._registry[name] = (effect_fn, metadata)

    @classmethod
    def get(cls, name: str) -> Callable | None:
        entry = cls._registry.get(name)
        if entry is not None:
            return entry[0]
        return None

    @classmethod
    def has(cls, name: str) -> bool:
        return name in cls._registry

    @classmethod
    def list(cls) -> list[tuple[str, dict]]:
        return [(name, meta) for name, (_, meta) in cls._registry.items()]

    @classmethod
    def compose(cls, names: list[str], frame: int, **kwargs) -> list[int]:
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
        cls._registry.clear()

    @classmethod
    def all_names(cls) -> list[str]:
        return list(cls._registry.keys())


def _register_default_effects() -> None:
    """注册默认效果到 EffectRegistry。模块加载时自动调用。"""
    from typing import Any

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
    "EffectRegistry",
]
