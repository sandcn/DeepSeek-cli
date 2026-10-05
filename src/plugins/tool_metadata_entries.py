"""工具元数据条目插件 — 清单中每个内置工具一条独立元数据条目。

「一切皆插件」：工具元数据（``parallel_safe`` / ``requires_network`` /
``requires_terminal`` / ``timeout_estimate`` / ``category`` / ``priority`` /
``tool_category`` / ``description``）不再硬编码在各工具类的 ``@tool_metadata``
装饰器里，而是由清单中的独立条目声明::

    - id: tool_metadata_bash
      plugin: src.plugins.tool_metadata_entries:apply_tool_metadata
      config:
        name: bash                          # 内置工具名（可被 patch/overlay 定位）
        # metadata: {priority: 5, ...}       # 可选：整条覆盖

插件挂载时把该工具名的内置元数据注册进注册表（``metadata=None`` 用默认声明）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置元数据
随之缺席（``tool_metadata`` 聚合插件经 ``managed_tool_metadata`` 抑制默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("tool_metadata_entry")
def apply_tool_metadata(ctx):
    from ..tools.metadata_registry import register_builtin_metadata

    name = ctx.config.get("name")
    if not name:
        raise ValueError("tool_metadata 条目缺少 config.name")
    metadata = ctx.config.get("metadata")
    undo = register_builtin_metadata(name, metadata)
    ctx.effect(lambda: undo)


__all__ = ["apply_tool_metadata"]
