"""技能来源插件 — 提供 ``ctx.skill_sources``。

「一切皆插件」：技能发现来源（项目 ``.skills`` / GitHub 安装 ``installed``）
从 ``SkillRegistry`` 的硬编码上移为内核服务 + 可替换注册表。内置来源由清单
中的独立条目（``skill_source``，经 ``src.plugins.skill_source_entries``）
声明，可按 Profile/Patch 禁用、覆盖或替换；外部插件可经
``ctx.skill_sources.register(name, factory)`` 注册自定义来源（如远程仓库、
运行时生成目录）。

``SkillRegistry.roots()`` 经 ``src.skills.source_registry.active_skill_sources``
解析本服务（内核缺失时回退进程级默认注册表）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class SkillSourcesService(Service):
    """技能来源服务 — 占据 ``ctx.skill_sources``。"""

    provide = "skill_sources"
    name = "skill_sources"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_skill_sources") or ()
        if managed:
            from ..skills.source_registry import set_managed_builtin_skill_sources

            undo_managed = set_managed_builtin_skill_sources(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_skill_sources") or ()
        self._disabled = list(disabled)
        if disabled:
            from ..skills.source_registry import disable_builtin_skill_sources

            undo_disabled = disable_builtin_skill_sources(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def source_names(self) -> list:
        """当前生效的来源名（内置 + 扩展）。"""
        from ..skills.source_registry import (
            active_skill_source_factories,
            skill_source_factories,
        )

        return sorted(set(active_skill_source_factories()) | set(skill_source_factories()))

    def builtin_source_ids(self) -> list:
        from ..skills.source_registry import builtin_skill_source_ids

        return list(builtin_skill_source_ids())

    def managed(self) -> list:
        from ..skills.source_registry import managed_skill_source_ids

        return list(managed_skill_source_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    # ── 解析 / 注册 ──────────────────────────────────────

    def resolve(self, registry, cwd=None) -> list:
        """按生效来源解析技能根目录列表（``registry`` 为 ``SkillRegistry``）。"""
        from ..skills.source_registry import resolve_skill_sources

        return list(resolve_skill_sources(registry, cwd))

    def register(self, name: str, factory) -> object:
        """注册扩展来源（注册即副作用，卸载时自动撤销）。"""
        from ..skills.source_registry import register_skill_source

        undo = register_skill_source(name, factory)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, name: str) -> bool:
        from ..skills.source_registry import unregister_builtin_skill_source, unregister_skill_source

        return unregister_skill_source(name) or unregister_builtin_skill_source(name)


@plugin("skill_sources", inject=["config"], provide=["skill_sources"])
def apply(ctx):
    return SkillSourcesService(ctx, ctx.config)


__all__ = ["SkillSourcesService", "apply"]
