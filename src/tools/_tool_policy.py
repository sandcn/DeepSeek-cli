"""Agent 类型工具策略 — 兼容 re-export 层。

实现已迁移至公共模块 ``tools.tool_policy``（消除跨包引用私有模块的命名
不一致）。本模块保留旧私有名 ``_TOOL_EXCLUSION_MAP`` / ``_get_excluded_tools``
兼容既有调用方；新代码请使用 ``tools.tool_policy``。
"""

from __future__ import annotations

from .tool_policy import (  # noqa: F401
    TOOL_EXCLUSION_MAP as _TOOL_EXCLUSION_MAP,
    get_excluded_tools as _get_excluded_tools,
)

__all__ = ["_TOOL_EXCLUSION_MAP", "_get_excluded_tools"]
