"""Agent 类型条目插件 — 清单中每个 SubAgent 类型一个独立插件条目。

「一切皆插件」：内置 SubAgent 类型（map/review/plan/execute）不再由
``src/core/agent_types.py`` 一次性默认装配，而是由清单中的独立条目声明::

    - id: agent_type_map
      plugin: src.plugins.agent_type_entries:apply_agent_type
      config:
        name: map                        # 内置类型 id（可被 patch/overlay 定位）
        # prompt_builder: build_map_agent_prompt   # 可选：覆盖提示词端口方法
        # agent_name: map                          # 可选：提示词文件基名
        # exclusions: [bash, write_file]           # 可选：覆盖排除工具集合
        # low_model: true                          # 可选：优先低优先级模型
        # path_whitelist: plan                     # 可选：写入路径白名单标识
        # mcp_agent_type: execute                  # 可选：MCP 章节权限类型

插件挂载时把该类型注册进 Agent 类型注册表（``spec=None`` 用默认声明）；卸载
时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置类型随之
缺席（``subagents`` 聚合插件经 ``managed_agent_types`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: 可由条目 config 覆盖的规格字段
_SPEC_FIELDS = (
    "description",
    "prompt_builder",
    "agent_name",
    "exclusions",
    "low_model",
    "path_whitelist",
    "mcp_agent_type",
)


def _spec_from_config(name: str, config: dict) -> Optional[object]:
    from ..core.agent_types import AgentTypeSpec, default_agent_type_spec

    overrides = {key: config[key] for key in _SPEC_FIELDS if key in config}
    if not overrides:
        return None
    try:
        base = default_agent_type_spec(name)
    except KeyError:
        base = AgentTypeSpec(name=name)
    data = base.to_dict()
    data.update(overrides)
    data["name"] = name
    exclusions = data.get("exclusions") or ()
    return AgentTypeSpec(
        name=name,
        description=str(data.get("description", "")),
        prompt_builder=str(data.get("prompt_builder", "")),
        agent_name=str(data.get("agent_name", "")),
        exclusions=tuple(str(item) for item in exclusions),
        low_model=bool(data.get("low_model", False)),
        path_whitelist=str(data.get("path_whitelist", "")),
        mcp_agent_type=str(data.get("mcp_agent_type", "")),
    )


@plugin("agent_type")
def apply_agent_type(ctx):
    from ..core.agent_types import register_builtin_agent_type

    name = ctx.config.get("name")
    if not name:
        raise ValueError("agent_type 条目缺少 config.name")
    undo = register_builtin_agent_type(name, _spec_from_config(name, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_agent_type"]
