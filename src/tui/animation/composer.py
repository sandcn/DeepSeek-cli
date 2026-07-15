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
from tui_framework.animation.composer import *

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
