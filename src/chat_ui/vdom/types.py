"""共享类型定义 — react_ink 子包的类型基础。

集中定义所有跨模块共享的 dataclass 和异常类：
  - HookState / EffectState — Hooks 运行时状态
  - LayoutBox — 布局计算结果
  - HookError / LayoutError — 子包专用异常

子包内部通过相对导入（from ._types import ...）引用这些类型。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


# ── 异常类 ─────────────────────────────────────────────

class HookError(RuntimeError):
    """Hooks 运行时错误 — 在非法的 Hook 调用场景下抛出。

    例如：在 render 外调用 Hook、Hook 调用顺序不一致等。
    """
    pass


class LayoutError(RuntimeError):
    """布局计算错误 — 在布局引擎计算失败时抛出。

    例如：容器尺寸不足、非法布局属性值等。
    """
    pass


# ── Hooks 状态类型 ──────────────────────────────────────

@dataclass
class HookState:
    """单个 Hook 的运行时状态。

    Attributes:
        type: Hook 类型标识（'state' / 'effect' / 'ref' / 'memo' / 'callback' / 'context' / 'reducer'）。
        value: Hook 的当前值。
        deps: 依赖数组（用于 effect / memo / callback 的浅比较）。
    """
    type: str
    value: Any
    deps: list[Any] | None = None
    cleanup: Callable[[], None] | None = None


@dataclass
class EffectState:
    """Effect Hook 的运行时状态。

    Attributes:
        effect_fn: 副作用函数。
        cleanup_fn: 上次渲染的清理函数。
        deps: 当前依赖数组。
        prev_deps: 上次渲染的依赖数组（用于浅比较判断是否重新执行）。
    """
    effect_fn: Callable[[], Callable[[], None] | None]
    cleanup_fn: Callable[[], None] | None = None
    deps: list[Any] | None = None
    prev_deps: list[Any] | None = None


# ── 布局类型 ────────────────────────────────────────────

@dataclass
class LayoutBox:
    """标准化的布局计算结果。

    Attributes:
        x: 左上角列坐标。
        y: 左上角行坐标。
        width: 元素宽度（列数）。
        height: 元素高度（行数）。
        content_width: 内容自然宽度。
        content_height: 内容自然高度。
        z_index: 层叠顺序（越高越靠前显示，默认 0）。
        position: 定位模式（"relative" 参与正常流，"absolute" 脱离正常流）。
    """
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    content_width: int = 0
    content_height: int = 0
    z_index: int = 0
    position: str = "relative"


