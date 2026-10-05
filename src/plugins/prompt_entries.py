"""提示词条目插件 — 清单中每个运行模式 / 提词来源一个独立插件条目。

「一切皆插件」：主 Agent 运行模式（empty/simple/standard）与子代理静态提词
文件映射不再硬编码在 ``src/prompt_builder/builder.py`` 里，而是由清单中的
独立条目声明::

    - id: prompt_mode_simple
      plugin: src.plugins.prompt_entries:apply_prompt_mode
      config:
        name: simple                       # 内置模式 id（可被 patch/overlay 定位）
        # label: 简单模式                   # 可选：覆盖显示名
        # export: prompts_export_main_simple  # 可选：覆盖提词文件基名
        # order: 1                          # 可选：Ctrl+B 循环顺序

    - id: prompt_source_map
      plugin: src.plugins.prompt_entries:apply_prompt_source
      config:
        name: map                          # 内置 agent 名（可被 patch/overlay 定位）
        # export: prompts_export_map       # 可选：覆盖提词文件基名

插件挂载时把该项注册进对应注册表（``prompt_builder.modes`` /
``prompt_builder.sources``）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay
禁用即不挂载，对应模式/来源随之缺席（``prompt`` 聚合插件经
``managed_prompt_modes`` / ``managed_prompt_sources`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: prompt_mode 条目可由 config 覆盖的字段
_MODE_FIELDS = ("label", "export", "order")


def _mode_from_config(name: str, config: dict):
    from ..prompt_builder.modes import AgentMode, default_mode

    overrides = {key: config[key] for key in _MODE_FIELDS if key in config}
    if not overrides:
        return None
    try:
        base = default_mode(name)
    except KeyError:
        base = AgentMode(name=name, label=name, export="")
    data = base.to_dict()
    data.update(overrides)
    return AgentMode(
        name=name,
        label=str(data.get("label", name)),
        export=str(data.get("export", "")),
        order=int(data.get("order", 0)),
    )


@plugin("prompt_mode")
def apply_prompt_mode(ctx):
    from ..prompt_builder.modes import register_builtin_mode

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("prompt_mode 条目缺少 config.name")
    undo = register_builtin_mode(name, _mode_from_config(name, ctx.config))
    ctx.effect(lambda: undo)


@plugin("prompt_source")
def apply_prompt_source(ctx):
    from ..prompt_builder.sources import register_builtin_prompt_source

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("prompt_source 条目缺少 config.name")
    export: Optional[str] = ctx.config.get("export")
    undo = register_builtin_prompt_source(
        name, None if export is None else str(export)
    )
    ctx.effect(lambda: undo)


__all__ = ["apply_prompt_mode", "apply_prompt_source"]
