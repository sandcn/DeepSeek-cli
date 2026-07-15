"""声明式动效集成 — @effect 装饰器 + EffectBuilder。

提供将动画效果以声明式方式附加到 Widget 子类的能力。
通过 ``@effect`` 类装饰器声明效果元数据，
由 ``AnimatedWidget`` 在 ``did_mount()`` 时自动初始化。

设计原则：
  - 声明式：装饰器仅记录元数据，不产生副作用
  - 延迟绑定：效果实例在挂载时创建（此时 theme/animator 等资源已就绪）
  - 与 EffectRegistry 集成：效果类型映射到 core.effects 中的构建器

效果类型映射：
  - ``fade_in`` → 渐显（使用 FadeIn 过渡或 color 呼吸）
  - ``slide_in`` → 滑入（使用 SlideIn 过渡）
  - ``pulse`` → 脉冲（使用 sine_color 呼吸）
  - ``shimmer`` → 流光扫光（使用 shimmer_apply）
  - ``rainbow`` → 彩虹渐变（使用 rainbow_color）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


__all__ = [
    "effect",
    "EffectInstance",
    "EffectBuilder",
]


# ═══════════════════════════════════════════════════════════
# @effect 装饰器
# ═══════════════════════════════════════════════════════════


def effect(
    name: str,
    type: str = "fade_in",
    duration: int = 6,
    easing: str = "smooth",
) -> Callable:
    """声明式动效装饰器工厂。

    装饰 Widget 子类时自动注册效果元数据到 ``cls._declared_effects`` 列表。
    支持多次叠加——每个 ``@effect`` 调用追加一条元数据。

    Args:
        name: 效果名称（如 ``"expand"``、``"appear"``），
              用于 ``AnimatedWidget.trigger_effect(name)`` 触发。
        type: 效果类型，支持 ``"fade_in"`` / ``"slide_in"`` /
              ``"pulse"`` / ``"shimmer"`` / ``"rainbow"``。
        duration: 效果持续帧数（默认 6，10Hz 下约 0.6s）。
        easing: 缓动函数（``"smooth"``/``"bounce"``/``"linear"``）。

    Returns:
        类装饰器函数。

    Example:
        >>> @effect("appear", type="fade_in", duration=6, easing="smooth")
        ... class MyWidget(AnimatedWidget):
        ...     pass
    """
    SUPPORTED_TYPES = frozenset({"fade_in", "slide_in", "pulse", "shimmer", "rainbow"})
    if type not in SUPPORTED_TYPES:
        raise ValueError(
            f"不支持的效果类型: {type!r}，支持: {sorted(SUPPORTED_TYPES)}"
        )

    def decorator(cls: type) -> type:
        if not hasattr(cls, "_declared_effects"):
            cls._declared_effects = []  # type: ignore[attr-defined]
        cls._declared_effects.append({  # type: ignore[attr-defined]
            "name": name,
            "type": type,
            "duration": duration,
            "easing": easing,
        })
        return cls

    return decorator


# ═══════════════════════════════════════════════════════════
# EffectInstance — 效果运行时实例
# ═══════════════════════════════════════════════════════════


@dataclass
class EffectInstance:
    """单个效果运行时实例 — 跟踪激活状态和帧号。

    由 ``AnimatedWidget._init_effects()`` 从 ``_declared_effects``
    初始化，通过 ``trigger()`` 激活。

    Attributes:
        name: 效果名称（与 @effect 声明的 name 对应）。
        type: 效果类型（fade_in/slide_in/pulse/shimmer/rainbow）。
        duration: 持续帧数。
        easing: 缓动函数名。
        _frame: 当前帧号，-1 表示未激活，0..duration-1 表示激活中。
    """

    name: str
    type: str
    duration: int
    easing: str = "smooth"
    _frame: int = field(default=-1, init=False)

    @property
    def active(self) -> bool:
        """效果是否激活中。"""
        return self._frame >= 0 and self._frame < self.duration

    @property
    def frame(self) -> int:
        """当前帧号（激活中返回 0..duration-1，未激活返回 -1）。"""
        return self._frame

    @property
    def progress(self) -> float:
        """效果进度 [0.0, 1.0]，未激活返回 0.0。"""
        if not self.active or self.duration <= 0:
            return 0.0
        return min(1.0, self._frame / max(self.duration - 1, 1))

    def trigger(self) -> None:
        """激活效果（reset frame to 0）。"""
        self._frame = 0

    def reset(self) -> None:
        """重置效果为未激活状态。"""
        self._frame = -1

    def tick(self) -> None:
        """推进一帧（仅在激活状态下有效）。"""
        if self.active:
            self._frame += 1

    def apply(self, content: str) -> str:
        """对内容文本施加当前帧的效果。

        根据效果类型选择不同的渲染策略：

        - ``fade_in``: 整段文本渐显（逐帧变亮）
        - ``slide_in``: 文本从左侧滑入（逐帧增加可见字符）
        - ``pulse``: 文本整体呼吸闪烁
        - ``shimmer``: 对文本施加流光扫光（逐字符）
        - ``rainbow``: 文本彩虹渐变旋转

        Args:
            content: 待施加效果的原始文本内容。

        Returns:
            施加效果后的 ANSI 文本。
        """
        if not self.active or not content:
            return content

        if self.type == "fade_in":
            return self._apply_fade_in(content)
        elif self.type == "slide_in":
            return self._apply_slide_in(content)
        elif self.type == "pulse":
            return self._apply_pulse(content)
        elif self.type == "shimmer":
            return self._apply_shimmer(content)
        elif self.type == "rainbow":
            return self._apply_rainbow(content)
        return content

    # ── 各效果类型实现 ──────────────────────────────────

    def _eased_t(self) -> float:
        """计算当前帧的缓动归一值 [0.0, 1.0]。"""
        t = self.progress
        if self.easing == "bounce":
            from ..core.effects import bounce_easing
            return bounce_easing(t)
        elif self.easing == "smooth":
            from ..core.effects import sine_easing
            return sine_easing(t)
        return t  # linear

    def _apply_fade_in(self, content: str) -> str:
        """渐显效果：整体内容从暗到亮。"""
        from ..core.effects import sine_easing
        t = sine_easing(self.progress)
        start_color, end_color = 238, 255
        color = round(start_color + t * (end_color - start_color))
        color = max(0, min(255, color))
        return f"\033[38;5;{color}m{content}\033[0m"

    def _apply_slide_in(self, content: str) -> str:
        """滑入效果：内容从左侧逐字符揭示。

        对多行内容逐行施加滑入效果，保留换行结构。
        边框等结构线不受影响（检查首字符是否为 ANSI 序列）。
        """
        if '\n' not in content:
            return self._slide_single_line(content)

        lines = content.split('\n')
        result_lines: list[str] = []
        for line in lines:
            # 带边框的结构线保持不变（以 ANSI + box char 开头）
            stripped = line
            # Skip ANSI prefix
            ansi_prefix = ""
            if line.startswith('\033['):
                end = line.find('m')
                if end != -1:
                    ansi_prefix = line[:end + 1]
                    stripped = line[end + 1:]
            if stripped and stripped[0] in '┌└├│┘┐┤─':
                result_lines.append(line)
            else:
                result_lines.append(ansi_prefix + self._slide_single_line(stripped))
        return '\n'.join(result_lines)

    def _slide_single_line(self, content: str) -> str:
        """对单行文本施加滑入效果。"""
        t = self._eased_t()
        reveal = round(t * len(content))
        reveal = max(0, min(reveal, len(content)))
        if reveal <= 0:
            return ""
        if reveal >= len(content):
            return content
        return content[:reveal]

    def _apply_pulse(self, content: str) -> str:
        """脉冲效果：内容色号呼吸闪烁。"""
        from ..core.effects import sine_color
        color = sine_color(self._frame, 238, 255, self.duration)
        return f"\033[38;5;{color}m{content}\033[0m"

    def _apply_shimmer(self, content: str) -> str:
        """流光扫光：亮带沿文本方向移动。"""
        from ..core.effects import shimmer_apply, shimmer_position

        # 为每个字符构建色号
        colors = [45] * len(content)  # 基准青色
        center = shimmer_position(self._frame, len(content), speed=1.0)
        width = max(3, len(content) // 3)

        parts: list[str] = []
        for i, ch in enumerate(content):
            dist = abs(i - center)
            if dist < width:
                factor = 1.0 - dist / width
                boost = round(40 * factor)
                c = max(0, min(255, 45 + boost))
            else:
                c = 45
            parts.append(f"\033[38;5;{c}m{ch}")
        parts.append("\033[0m")
        return "".join(parts)

    def _apply_rainbow(self, content: str) -> str:
        """彩虹渐变：每个字符颜色按彩虹色环旋转。"""
        from ..core.effects import rainbow_color
        parts: list[str] = []
        for i, ch in enumerate(content):
            c = rainbow_color(self._frame, i, 8)
            parts.append(f"\033[38;5;{c}m{ch}")
        parts.append("\033[0m")
        return "".join(parts)


# ═══════════════════════════════════════════════════════════
# EffectBuilder — 效果构建器
# ═══════════════════════════════════════════════════════════


class EffectBuilder:
    """效果构建器 — 将 ``_declared_effects`` 元数据转为 ``EffectInstance``。

    与 ``EffectRegistry`` 集成：通过效果类型名查找注册的效果函数，
    构建对应的效果实例。若类型未注册则使用 ``EffectInstance`` 内置实现。
    """

    #: 效果类型 → core.effects 中 EffectRegistry 的注册名映射
    TYPE_TO_REGISTRY: dict[str, str] = {
        "fade_in": "glow",
        "pulse": "pulse",
        "shimmer": "shimmer",
        "rainbow": "rainbow",
        "slide_in": None,  # 无对应注册效果，使用内置实现
    }

    @classmethod
    def build(cls, meta: dict[str, Any]) -> EffectInstance:
        """从声明元数据构建效果实例。

        Args:
            meta: ``_declared_effects`` 中的单条元数据字典。

        Returns:
            EffectInstance 实例。
        """
        return EffectInstance(
            name=meta["name"],
            type=meta["type"],
            duration=meta.get("duration", 6),
            easing=meta.get("easing", "smooth"),
        )

    @classmethod
    def get_effect_fn(cls, type_name: str) -> Callable | None:
        """从 EffectRegistry 获取效果类型对应的函数。

        Args:
            type_name: 效果类型名。

        Returns:
            效果函数，未注册时返回 None。
        """
        registry_name = cls.TYPE_TO_REGISTRY.get(type_name)
        if registry_name is None:
            return None
        from ..core.effects import EffectRegistry
        return EffectRegistry.get(registry_name)
