"""动效原语模块 — 所有动画效果的纯函数实现。

集中管理所有动画动效的计算逻辑，消除散落在各显示组件中
的重复实现。所有函数为纯计算，不依赖 AnimatorContext/
BreathPalette，直接接受帧号作为参数，可独立测试。

【已重构】effects.py 已按效果类别拆分为 3 个子模块（2026-07-17），
  EffectRegistry 合成器已内联至此文件：
  - _wave.py — 呼吸/正弦/波动效果（sine_color, build_glow_ansi 等）
  - _sparkle.py — 闪烁/脉冲/高亮效果（sparkle_color 等）
  - _train.py — 列车/扫光/流动效果（build_pulse_train_ansi 等）
  effects.py 保留为统一重导出入口，保持向后兼容。
  新代码可直接从子模块导入以获得更精确的依赖。

设计原则：
  - 纯函数：输入帧号 → 输出值/ANSI字符串，无副作用
  - 可缓存：热点动效使用 @lru_cache 减少重复计算
  - 窄屏安全：所有 ANSI 生成函数检查窄屏条件
  - 无 I/O：不涉及终端写入，仅生成 ANSI 序列
"""

from __future__ import annotations

from typing import Callable, ClassVar

from ._wave import (
    apply_wave,
    bounce_easing,
    bounce_frame_color,
    build_bg_breath_ansi,
    build_fade_in_ansi_enhanced,
    build_fg_breath_ansi,
    build_glow_ansi,
    build_wave_sep_ansi,
    get_theme_effect_color,
    sine_breath_t,
    sine_color,
    sine_color_range,
    sine_easing,
    wave_offset,
)

from ._sparkle import (
    sparkle_brightness,
    sparkle_color,
)

from ._train import (
    apply_heat_wave,
    aurora_color,
    build_aurora_ansi,
    build_aurora_gradient,
    build_heat_wave_ansi,
    build_matrix_rain_ansi,
    build_neon_border_ansi,
    build_pulse_train_ansi,
    build_rainbow_ansi,
    build_shimmer_sep_ansi,
    build_typewriter_ansi,
    heat_wave_offset,
    matrix_rain_color,
    neon_color,
    pulse_train,
    rainbow_color,
    shimmer_apply,
    shimmer_position,
    typewriter_cursor,
)

__all__ = [
    # 正弦波工具
    "sine_breath_t", "sine_color", "sine_color_range",
    # 弹入
    "bounce_easing", "bounce_frame_color",
    # 缓动
    "sine_easing",
    # 波动
    "wave_offset", "apply_wave",
    # 闪烁
    "sparkle_brightness", "sparkle_color",
    # 流光
    "shimmer_position", "shimmer_apply",
    # ANSI 生成器
    "build_fade_in_ansi_enhanced",
    "build_wave_sep_ansi",
    "build_shimmer_sep_ansi",
    "build_glow_ansi",
    "build_fg_breath_ansi",
    "build_bg_breath_ansi",
    # 主题动效消费者
    "get_theme_effect_color",
    # 新增渲染效果（2026-07-15 框架整合）
    "rainbow_color", "build_rainbow_ansi",
    "pulse_train", "build_pulse_train_ansi",
    "matrix_rain_color", "build_matrix_rain_ansi",
    "heat_wave_offset", "apply_heat_wave", "build_heat_wave_ansi",
    "aurora_color", "build_aurora_gradient", "build_aurora_ansi",
    # 霓虹 + 打字机效果（2026-07-15）
    "neon_color", "build_neon_border_ansi",
    "typewriter_cursor", "build_typewriter_ansi",
    # 效果注册表
    "EffectRegistry",
]


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
