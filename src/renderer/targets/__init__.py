"""targets — 统一渲染目标抽象层。

将渲染输出抽象为 RenderTarget 接口，支持多端输出：
  - TerminalRenderTarget: 终端（Rich Console）
  - WebRenderTarget: Web（HTML）
  - CompositeRenderTarget: 多目标组合

架构：
  VNodePatcher / IncrementalVNodeRenderer
        │
        ▼  (通过 RenderTarget 接口)
  ┌─────────────┐  ┌────────────┐  ┌────────────────┐
  │Terminal     │  │Web         │  │Composite       │
  │RenderTarget │  │RenderTarget│  │RenderTarget    │
  └─────────────┘  └────────────┘  └────────────────┘
"""

from __future__ import annotations

from .base import RenderTarget, CompositeRenderTarget, RenderTargetContext
from .terminal import TerminalRenderTarget
from .web import WebRenderTarget

__all__ = [
    "RenderTarget",
    "CompositeRenderTarget",
    "RenderTargetContext",
    "TerminalRenderTarget",
    "WebRenderTarget",
]
