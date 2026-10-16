"""代理内部实现 — 工具回调、子代理生成、输出捕获"""
from __future__ import annotations

from ._capture_manager import CaptureManager  # noqa: F401 — 显式重导出（替代 star import）
from ._subagent_spawner import SubAgentSpawner  # noqa: F401 — 显式重导出
from ._tool_callbacks import ToolCallbackChain  # noqa: F401 — 显式重导出

__all__ = [
    # _tool_callbacks
    "ToolCallbackChain",
    # _subagent_spawner
    "SubAgentSpawner",
    # _capture_manager
    "CaptureManager",
]
