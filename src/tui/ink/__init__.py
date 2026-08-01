"""ink — React Ink 风格组件框架核心（零 Rich 依赖）。

组件树 + 调和器（reconciler）+ hooks + flexbox 布局 + 帧差异渲染，
非全屏（随内容流动）模型。由以下子模块组成：
  - element.py    — 不可变元素（Element/h）
  - fiber.py      — 调和器工作单元（Fiber/hook 节点）
  - hooks.py      — use_state/use_effect/use_ref/use_reducer
  - reconciler.py — 挂载/更新 fiber 树 + effect 队列
  - layout.py     — flexbox 子集 + 文本换行
  - output.py     — StyledRun/Line/Frame 输出模型
  - helpers.py    — ANSI 剥离 / 宽度测量 / 换行截断
  - components.py — host 组件渲染函数
  - renderer.py   — InkRenderer 非全屏渲染器（行级 diff）
  - diff.py       — 新旧 Frame 行级 diff
  - session.py    — InkSession（PriorityQueue + render 线程 + 生命周期）
"""

from __future__ import annotations

from .element import (
    BOX,
    TEXT,
    STATIC,
    SPACER,
    APP,
    Element,
    ElementType,
    Child,
    h,
)
from .output import StyledRun, Line, Frame, FrameBuilder
from .helpers import (
    strip_ansi,
    has_ansi,
    visual_width,
    wrap_runs_by_width,
    truncate_runs,
    truncate_line,
    pad_line,
    line_to_ansi,
)
from .registry import register_host, unregister_host, get_host, has_host

__all__ = [
    # element
    "BOX",
    "TEXT",
    "STATIC",
    "SPACER",
    "APP",
    "Element",
    "ElementType",
    "Child",
    "h",
    # output
    "StyledRun",
    "Line",
    "Frame",
    "FrameBuilder",
    # helpers
    "strip_ansi",
    "has_ansi",
    "visual_width",
    "wrap_runs_by_width",
    "truncate_runs",
    "truncate_line",
    "pad_line",
    "line_to_ansi",
    # registry
    "register_host",
    "unregister_host",
    "get_host",
    "has_host",
]
