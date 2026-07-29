"""RenderBuffer 渲染缓冲区 — 向后兼容 re-export。

迁移说明（2026-07-29 TUI 重构）：
  - 实现已迁移至 src/tui/_buffer.py
  - 本模块作为向后兼容的 re-export 存根
"""

from __future__ import annotations

from ._buffer import RenderBuffer

__all__ = ["RenderBuffer"]
