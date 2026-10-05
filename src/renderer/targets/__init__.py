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
from .file import FileRenderTarget
from .registry import (
    active_render_target,
    builtin_render_target_factories,
    builtin_render_target_ids,
    disable_builtin_render_targets,
    managed_render_target_ids,
    register_builtin_render_target,
    register_render_target,
    render_target_factories,
    resolve_render_target,
    set_managed_builtin_render_targets,
    unregister_builtin_render_target,
)
from .terminal import TerminalRenderTarget

__all__ = [
    "RenderTarget",
    "CompositeRenderTarget",
    "RenderTargetContext",
    "TerminalRenderTarget",
    "FileRenderTarget",
    "active_render_target",
    "builtin_render_target_factories",
    "builtin_render_target_ids",
    "disable_builtin_render_targets",
    "managed_render_target_ids",
    "register_builtin_render_target",
    "register_render_target",
    "render_target_factories",
    "resolve_render_target",
    "set_managed_builtin_render_targets",
    "unregister_builtin_render_target",
]
