"""输出目标抽象接口 — 向后兼容存根（从 src.tui.core.output_target 重新导出）

变更说明：输出目标已迁移到 src/tui/core/output_target.py，此文件保留为向后兼容存根。
"""
from __future__ import annotations

from ..tui.core.output_target import (
    IOutputTarget, TerminalTarget, BufferTarget, NullTarget,
)

__all__ = ["IOutputTarget", "TerminalTarget", "BufferTarget", "NullTarget"]
