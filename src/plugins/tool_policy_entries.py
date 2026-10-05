"""全局禁用工具条目插件 — 清单中每个禁用项一个独立插件条目。

「一切皆插件」：全局禁用工具集合（对任何 agent 都不可加载的工具，如 cordis
工具族）不再只是 ``tools/tool_policy.py`` 的模块常量，而是由清单中的独立条目
声明::

    - id: global_disabled_tool_cordis_inspect
      plugin: src.plugins.tool_policy_entries:apply_global_disabled_tool
      config:
        name: cordis_inspect              # 内置项名（可被 patch/overlay 定位）

插件挂载时把该项注册进全局禁用工具注册表（工具发现阶段据此剔除）；卸载时撤销
（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应工具随之**解除**全局禁用
（``policy`` 聚合插件经 ``managed_global_disabled_tools`` 抑制默认装配）。

策略插件 config ``globally_disabled_tools`` 仍提供整体显式覆盖（最高优先，
用于「只禁用某几个、其余放行」的场景）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("global_disabled_tool")
def apply_global_disabled_tool(ctx):
    from ..tools.tool_policy import register_builtin_global_disabled_tool

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("global_disabled_tool 条目缺少 config.name")
    undo = register_builtin_global_disabled_tool(name)
    ctx.effect(lambda: undo)


__all__ = ["apply_global_disabled_tool"]
