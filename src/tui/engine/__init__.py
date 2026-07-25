"""engine — TUI 渲染引擎层。

职责范围：
- TuiEngine — render 线程 + 命令队列（生产者-消费者模式）
- TuiRenderer — 聊天域渲染命令分发（继承 FrameworkRenderer）
- FrameworkRenderer — 框架通用渲染器基类（ComponentRegistry 驱动）
- EventDispatcher — 事件→渲染命令映射（可注册/可扩展模式）

架构分层：
  FrameworkRenderer (renderer_base.py) — 框架通用基类
    └── TuiRenderer (renderer.py)      — 聊天域子类

  RenderCommand (const.py)             — 全部 20 个渲染命令枚举
  FrameworkCommand (const.py)          — RenderCommand 别名（向后兼容）
"""

from __future__ import annotations

# ── 引擎核心 ──────────────────────────────────────
from .engine import TuiEngine

# ── 渲染器 ────────────────────────────────────────
from .renderer import TuiRenderer
from .renderer_base import FrameworkRenderer

# ── 事件分发 ──────────────────────────────────────
from .dispatcher import EventDispatcher

# ── 命令枚举 ──────────────────────────────────────
from .const import RenderCommand, FrameworkCommand

__all__ = [
    "TuiEngine",
    "TuiRenderer",
    "FrameworkRenderer",
    "EventDispatcher",
    "RenderCommand",
    "FrameworkCommand",
]
