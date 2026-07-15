"""动画合成器模块 — 组合动效原语，构建复杂动画效果。

提供三个层次的能力：
  1. AnimationEffect Protocol：所有效果类须实现的接口契约
  2. 基础合成效果类：CompositeEffect（并行）、EffectChain（顺序）、InterleaveEffect（交替）
  3. 工厂函数：anim_parallel / anim_sequence / anim_loop 降低使用成本

设计原则：
  - 组合优于继承：效果通过组合（CompositeEffect）和链式（EffectChain）而非深继承
  - Protocol 解耦：AnimationEffect 使用 typing.Protocol 支持结构类型匹配
  - 零 I/O：所有效果类为纯渲染，仅接受 frame 参数返回 ANSI 字符串
  - 窄屏安全：工厂函数和效果类不直接检测窄屏（由底层效果自行处理）

依赖关系：
  - 不直接依赖 effects.py（由具体效果实现类引用）
  - 不依赖动画时钟/帧管理器（调用方提供 frame 参数）
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "AnimationEffect",
    "CompositeEffect",
    "EffectChain",
    "InterleaveEffect",
    "anim_parallel",
    "anim_sequence",
    "anim_loop",
]


@runtime_checkable
class AnimationEffect(Protocol):
    """动画效果协议接口。

    所有效果类（包括本模块的合成效果和 transitions 中的过渡效果）
    均实现此协议，支持结构类型匹配而非强制继承。

    实现约定：
      - render(frame) 必须接受整数帧号，返回 ANSI 字符串
      - frame ≥ total_frames 时应返回空字符串（效果结束）
      - 窄屏时内部自行降级（非协议强制，但为推荐实践）
    """

    def render(self, frame: int) -> str: ...


@dataclass(frozen=True)
class CompositeEffect:
    """并行合成效果 — 所有子效果在同一帧同时渲染，用 separator 拼接。"""

    effects: list[AnimationEffect]
    separator: str = " "

    def render(self, frame: int) -> str:
        parts: list[str] = []
        for effect in self.effects:
            result = effect.render(frame)
            if result:
                parts.append(result)
        if not parts:
            return ""
        return self.separator.join(parts)


@dataclass(frozen=True)
class EffectChain:
    """顺序链式播放效果 — 按 durations 依次播放每个子效果。"""

    effects: list[AnimationEffect]
    durations: list[int]
    loop: bool = False

    def __post_init__(self) -> None:
        if len(self.effects) != len(self.durations):
            raise ValueError(
                f"effects ({len(self.effects)}) 与 durations ({len(self.durations)}) 长度不一致"
            )

    def _resolve_frame(self, frame: int) -> tuple[int, int]:
        total_duration = sum(self.durations)
        if total_duration <= 0:
            return (0, frame)

        if self.loop:
            frame = frame % total_duration
        else:
            if frame >= total_duration:
                local = frame - (total_duration - self.durations[-1])
                return (len(self.effects) - 1, local)

        accumulated = 0
        for i, dur in enumerate(self.durations):
            accumulated += dur
            if frame < accumulated:
                prev_total = sum(self.durations[:i])
                return (i, frame - prev_total)

        return (len(self.effects) - 1, frame)

    def render(self, frame: int) -> str:
        if not self.effects:
            return ""

        idx, local_frame = self._resolve_frame(frame)
        if idx < 0 or idx >= len(self.effects):
            return ""

        return self.effects[idx].render(local_frame)


@dataclass(frozen=True)
class InterleaveEffect:
    """交替渲染效果 — 按固定间隔交替播放两个子效果。"""

    a: AnimationEffect
    b: AnimationEffect
    interval: int = 1

    def render(self, frame: int) -> str:
        if (frame // self.interval) % 2 == 0:
            return self.a.render(frame)
        return self.b.render(frame)


class _LoopEffect:
    """周期循环效果包装器（不导出，内部使用）。"""

    __slots__ = ("effect", "period")

    def __init__(self, effect: AnimationEffect, period: int = 12) -> None:
        self.effect = effect
        self.period = period

    def render(self, frame: int) -> str:
        return self.effect.render(frame % self.period)


def anim_parallel(
    *effects: AnimationEffect,
    separator: str = " ",
) -> CompositeEffect:
    """创建并行合成效果。"""
    return CompositeEffect(list(effects), separator=separator)


def anim_sequence(
    *effects: AnimationEffect,
    durations: list[int] | None = None,
    loop: bool = False,
) -> EffectChain:
    """创建顺序链式播放效果。"""
    if not effects:
        raise ValueError("anim_sequence 至少需要一个效果")

    if durations is None:
        durations = [6] * len(effects)
    elif len(durations) != len(effects):
        raise ValueError(
            f"durations 长度 ({len(durations)}) 与 effects 数量 ({len(effects)}) 不匹配"
        )

    return EffectChain(list(effects), durations, loop=loop)


def anim_loop(
    effect: AnimationEffect,
    period: int = 12,
) -> _LoopEffect:
    """创建周期循环效果。"""
    return _LoopEffect(effect, period=period)
