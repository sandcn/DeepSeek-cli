"""工具显示名映射（核心层公共模块）。

工具注册名（snake_case）→ UI 显示名（PascalCase）映射，供核心层与表现层
展示工具名使用；``tools.registry`` / ``tools._constants`` 从此导入并
re-export 兼容。

「一切皆插件」：映射不再是本模块的硬编码字典，而是
``src.presentation_data`` 注册表中的一张数据表（``tool_display_name``）——
由清单中的独立插件条目（``presentation_data``）注册，可被 Profile/Patch/
Overlay 按 id 覆盖（整表替换）或禁用。``TOOL_DISPLAY_NAME`` 为**实时委托
视图**（保留旧调用面：``[]`` / ``in`` / ``.get`` / 迭代）。
"""

from __future__ import annotations

from ..presentation_data import LiveMapping

TOOL_DISPLAY_NAME = LiveMapping("tool_display_name")


def get_tool_display_name(tool_name: str) -> str:
    """获取工具在 UI 上显示的完整名称（PascalCase）；无映射返回原名称。"""
    return TOOL_DISPLAY_NAME.get(tool_name, tool_name)


__all__ = ["TOOL_DISPLAY_NAME", "get_tool_display_name"]
