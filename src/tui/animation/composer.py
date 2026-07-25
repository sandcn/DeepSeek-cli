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
    # Protocol
    "AnimationEffect",
    # 合成效果类
    "CompositeEffect",
    "EffectChain",
    "InterleaveEffect",
    # 工厂函数
    "anim_parallel",
    "anim_sequence",
    "anim_loop",
]


# ═══════════════════════════════════════════════════════════
# AnimationEffect Protocol
# ═══════════════════════════════════════════════════════════


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

    def render(self, frame: int) -> str:
        """渲染指定帧的动画输出。

        Args:
            frame: 当前帧号（0-based），由调用方（AnimatorContext 等）推进。

        Returns:
            ANSI 格式的渲染结果字符串。无输出时返回空字符串。
        """


# ═══════════════════════════════════════════════════════════
# CompositeEffect — 并行合成
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CompositeEffect:
    """并行合成效果 — 所有子效果在同一帧同时渲染，用 separator 拼接。

    适用于需要同时展示多个动画效果的场景，如：
      - 转轮 + 进度文案并行显示
      - 多个装饰元素的组合动画

    Args:
        effects: 子效果列表（均实现 AnimationEffect Protocol）。
        separator: 子效果输出之间的分隔符，默认空格。
    """

    effects: list[AnimationEffect]
    separator: str = " "

    def render(self, frame: int) -> str:
        """并行渲染所有子效果。

        Args:
            frame: 当前帧号。

        Returns:
            所有子效果渲染结果用 separator 拼接后的字符串。
            如所有子效果均返回空字符串，则返回空字符串。
        """
        parts: list[str] = []
        for effect in self.effects:
            result = effect.render(frame)
            if result:
                parts.append(result)
        if not parts:
            return ""
        return self.separator.join(parts)


# ═══════════════════════════════════════════════════════════
# EffectChain — 顺序链式播放
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class EffectChain:
    """顺序链式播放效果 — 按 durations 依次播放每个子效果。

    每段效果播放对应帧数后自动切换到下一个效果。
    全部播放完毕后根据 loop 参数决定是否循环（或保持最后一个效果）。

    适用于幻灯片式的逐帧动画序列。

    Args:
        effects: 子效果列表（均实现 AnimationEffect Protocol）。
        durations: 每个效果的持续帧数列表，须与 effects 等长。
        loop: 是否循环播放。True 时从头重新播放；False 时停在最后一个效果。
    """

    effects: list[AnimationEffect]
    durations: list[int]
    loop: bool = False

    def __post_init__(self) -> None:
        """验证 effects 和 durations 长度一致。"""
        if len(self.effects) != len(self.durations):
            raise ValueError(
                f"effects ({len(self.effects)}) 与 durations ({len(self.durations)}) 长度不一致"
            )

    def _resolve_frame(self, frame: int) -> tuple[int, int]:
        """解析帧号，返回 (活跃效果索引, 局部帧号)。

        Args:
            frame: 当前帧号。

        Returns:
            (索引, 局部帧号) 元组。超出总时长且非循环时停留在最后一个效果。
        """
        total_duration = sum(self.durations)
        if total_duration <= 0:
            return (0, frame)

        if self.loop:
            # 循环模式：对总时长取模
            frame = frame % total_duration
        else:
            # 非循环模式：超出总时长停在最后一个效果
            if frame >= total_duration:
                local = frame - (total_duration - self.durations[-1])
                return (len(self.effects) - 1, local)

        # 确定当前活跃效果索引
        accumulated = 0
        for i, dur in enumerate(self.durations):
            accumulated += dur
            if frame < accumulated:
                # 局部帧号 = 当前帧 - 之前所有效果的累计帧数
                prev_total = sum(self.durations[:i])
                return (i, frame - prev_total)

        # 兜底（不会到达此处）
        return (len(self.effects) - 1, frame)

    def render(self, frame: int) -> str:
        """渲染当前帧对应的活跃效果。

        Args:
            frame: 当前帧号。

        Returns:
            当前活跃效果的渲染结果。
        """
        if not self.effects:
            return ""

        idx, local_frame = self._resolve_frame(frame)
        if idx < 0 or idx >= len(self.effects):
            return ""

        return self.effects[idx].render(local_frame)


# ═══════════════════════════════════════════════════════════
# InterleaveEffect — 交替渲染
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class InterleaveEffect:
    """交替渲染效果 — 按固定间隔交替播放两个子效果。

    适用于 ABAB 交替的视觉节奏，如两种颜色的交替闪烁，
    或两个动画片段的交替展示。

    Args:
        a: 第一个子效果。
        b: 第二个子效果。
        interval: 交替间隔帧数（每个效果连续播放的帧数），默认 1。
    """

    a: AnimationEffect
    b: AnimationEffect
    interval: int = 1

    def render(self, frame: int) -> str:
        """渲染当前帧的活跃效果（按交替规则）。

        Args:
            frame: 当前帧号。

        Returns:
            interval 为奇数段时渲染 a，偶数段时渲染 b。
            具体规则：(frame // interval) % 2 == 0 → 渲染 a，否则渲染 b。
        """
        if (frame // self.interval) % 2 == 0:
            return self.a.render(frame)
        return self.b.render(frame)


# ═══════════════════════════════════════════════════════════
# 内部类：_LoopEffect — 周期循环包装
# ═══════════════════════════════════════════════════════════


class _LoopEffect:
    """周期循环效果包装器（不导出，内部使用）。

    将任意 AnimationEffect 的帧号按 period 取模后再传入，
    实现周期循环播放。

    Args:
        effect: 被包装的效果。
        period: 循环周期（帧数）。
    """

    __slots__ = ("effect", "period")

    def __init__(self, effect: AnimationEffect, period: int = 12) -> None:
        self.effect = effect
        self.period = period

    def render(self, frame: int) -> str:
        """渲染循环周期内的第 (frame % period) 帧。

        Args:
            frame: 全局帧号。

        Returns:
            效果在周期帧上的渲染结果。
        """
        return self.effect.render(frame % self.period)


# ═══════════════════════════════════════════════════════════
# 工厂函数 — 便捷创建入口
# ═══════════════════════════════════════════════════════════


def anim_parallel(
    *effects: AnimationEffect,
    separator: str = " ",
) -> CompositeEffect:
    """创建并行合成效果。

    所有子效果在同一帧同时渲染，输出用 separator 拼接。

    Args:
        *effects: 要并行渲染的子效果（变长参数）。
        separator: 子效果输出间的分隔符，默认空格。

    Returns:
        CompositeEffect 实例。
    """
    return CompositeEffect(list(effects), separator=separator)


def anim_sequence(
    *effects: AnimationEffect,
    durations: list[int] | None = None,
    loop: bool = False,
) -> EffectChain:
    """创建顺序链式播放效果。

    每个效果播放指定帧数后自动切换到下一个。
    全部播完根据 loop 决定是否循环。

    Args:
        *effects: 要顺序播放的子效果（变长参数，至少一个）。
        durations: 每个效果的持续帧数列表。None 时每效果默认 6 帧。
        loop: 是否循环播放，默认 False。

    Returns:
        EffectChain 实例。

    Raises:
        ValueError: effects 为空时抛出。
    """
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
    """创建周期循环效果。

    将帧号按 period 取模后再传入 effect.render()，
    使有限帧数的效果循环播放。

    Args:
        effect: 要循环的效果。
        period: 循环周期帧数，默认 12。

    Returns:
        _LoopEffect 实例（内部类，不导出但实现 AnimationEffect Protocol）。
    """
    return _LoopEffect(effect, period=period)
