"""engine — TUI 渲染引擎层。

职责范围：
- TuiEngine — render 线程 + 命令队列（生产者-消费者模式）
- TuiRenderer — 聊天域渲染命令分发（继承 FrameworkRenderer）
- FrameworkRenderer — 框架通用渲染器基类（ComponentRegistry 驱动）
- EventDispatcher — 事件→渲染命令映射（可注册/可扩展模式）
- RenderCommand — 全部 20 个渲染命令枚举（向后兼容）
- FrameworkCommand — 框架通用命令枚举（NOTIFICATION/WRITE_LINE/ERROR/SPLASH/SUBAGENT_FRAME）

架构分层（2026-07-22 泛化）：
  FrameworkRenderer (renderer_base.py) — 框架通用基类
    └── TuiRenderer (renderer.py)      — 聊天域子类

  FrameworkCommand (commands.py)       — 框架通用命令
  RenderCommand (const.py)             — 全部命令（向后兼容）
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
from .const import RenderCommand
from .commands import FrameworkCommand

__all__ = [
    "TuiEngine",
    "TuiRenderer",
    "FrameworkRenderer",
    "EventDispatcher",
    "RenderCommand",
    "FrameworkCommand",
]
