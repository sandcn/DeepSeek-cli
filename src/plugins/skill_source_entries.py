"""技能来源条目插件 — 清单中每个内置来源一个独立插件条目。

「一切皆插件」：内置技能来源（project/installed）不再硬编码在
``SkillRegistry.roots()`` 里，而是由清单中的独立条目声明::

    - id: skill_source_project
      plugin: src.plugins.skill_source_entries:apply_skill_source
      config:
        id: project                        # 内置来源 id（可被 patch/overlay 定位）
        # source: my_pkg.my_source          # 可选：替换实现（点分路径）

插件挂载时把该 id 的内置来源注册进技能来源注册表（``source=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置来源随之缺席（``skill_sources`` 聚合插件经 ``managed_skill_sources``
抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[Any, Any], list]]:
    ref = config.get("source")
    if not ref:
        return None

    def _factory(registry, cwd=None):
        from .tool_plugin import import_attr

        return import_attr(ref)(registry, cwd)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("skill_source", inject=["skill_sources"])
def apply_skill_source(ctx):
    from ..skills.source_registry import register_builtin_skill_source

    spec_id = ctx.config.get("id") or ctx.config.get("name")
    if not spec_id:
        raise ValueError("skill_source 条目缺少 config.id")
    undo = register_builtin_skill_source(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_skill_source"]
