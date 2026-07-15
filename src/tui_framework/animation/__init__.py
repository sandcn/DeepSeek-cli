"""Animation 模块 — 动画系统。

子模块列表：

| 子模块 | 说明 |
|--------|------|
| composer.py | AnimationComposer — 动效组合器，编排多效果协同 |
| transitions.py | 过渡效果 — 缓动函数与过渡计算 |
| declarative.py | @effect 装饰器 + EffectRegistry 集成 — 声明式动效注入 |

声明式动效：
  - @effect(name, type, duration, easing) 装饰器工厂
  - 支持效果类型：fade_in / slide_in / pulse / shimmer / rainbow
  - AnimatedWidget 基类自动注入动效状态到渲染管线
"""
