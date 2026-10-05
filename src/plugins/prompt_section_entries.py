"""提示词片段条目插件 — 清单中每个系统提词片段一个独立插件条目。

「一切皆插件」：系统提词的片段顺序与装配（静态提词 / 项目摘要 / Agent 摘要 /
环境信息 / 版本控制 / 技能章节 / MCP 章节）不再硬编码在
``src.prompt_builder.builder._build_prompt`` 里，而是由清单中的独立条目声明::

    - id: prompt_section_env_info
      plugin: src.plugins.prompt_section_entries:apply_prompt_section
      config:
        name: env_info                      # 内置片段 id（可被 patch/overlay 定位）
        # order: 15                         # 可选：覆盖装配顺序
        # builder: my_pkg:my_section        # 可选：替换构造函数（点分引用）
        # label: 环境                         # 可选：覆盖显示名
        # attach_to: env_info               # 可选：追加到目标片段（同一 system 消息）

插件挂载时把该 id 的内置片段注册进片段注册表（``section=None`` 用默认声明）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应片段随之
缺席（``prompt`` 聚合插件经 ``managed_prompt_sections`` 抑制默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin

#: prompt_section 条目可由 config 覆盖的字段
_SECTION_FIELDS = ("label", "order", "builder", "attach_to", "description")


def _section_from_config(name: str, config: dict):
    from ..prompt_builder.sections import PromptSection, default_section

    overrides = {key: config[key] for key in _SECTION_FIELDS if key in config}
    if not overrides:
        return None
    try:
        base = default_section(name)
    except KeyError:
        base = PromptSection(id=name, label=name, builder="")
    data = base.to_dict()
    data.update(overrides)
    return PromptSection(
        id=name,
        label=str(data.get("label", name)),
        order=int(data.get("order", 0)),
        builder=str(data.get("builder", "")),
        attach_to=str(data.get("attach_to", "") or ""),
        description=str(data.get("description", "")),
    )


@plugin("prompt_section")
def apply_prompt_section(ctx):
    from ..prompt_builder.sections import register_builtin_section

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("prompt_section 条目缺少 config.name")
    undo = register_builtin_section(name, _section_from_config(name, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_prompt_section"]
