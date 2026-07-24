"""targets — 统一渲染目标抽象层。

将渲染输出抽象为 RenderTarget 接口，支持多端输出：
  - RenderTarget: 渲染目标基类
  - CompositeRenderTarget: 多目标组合
  - RenderTargetContext: 渲染目标上下文

架构：
  VNodePatcher / IncrementalVNodeRenderer
        │
        ▼  (通过 RenderTarget 接口)
  ┌────────────────┐  ┌────────────────┐
  │RenderTarget    │  │Composite       │
  │                │  │RenderTarget    │
  └────────────────┘  └────────────────┘
"""

from __future__ import annotations

from .base import RenderTarget, CompositeRenderTarget, RenderTargetContext

__all__ = [
    "RenderTarget",
    "CompositeRenderTarget",
    "RenderTargetContext",
]
