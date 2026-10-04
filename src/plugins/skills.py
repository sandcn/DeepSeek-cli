"""技能插件 — 提供 ``ctx.skills``。

包装 ``src/skills`` 技能注册表：分层发现、按需加载、运行时注册。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class SkillsService(Service):
    """技能服务 — 占据 ``ctx.skills``。"""

    provide = "skills"
    name = "skills"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..skills.registry import default_registry

        self._registry = default_registry()
        ctx.effect(lambda: self._registry.invalidate)

    @property
    def registry(self):
        return self._registry

    def enabled(self) -> bool:
        return self._registry.enabled()

    def list(self, cwd=None):
        return self._registry.list(cwd)

    def get(self, name: str, cwd=None):
        return self._registry.get(name, cwd=cwd)

    def register(self, name: str, description: str, content: str, **kwargs) -> bool:
        return self._registry.register(name, description, content, **kwargs)

    def catalog_entries(self, cwd=None, max_length: int = 500):
        return self._registry.catalog_entries(cwd, max_length)

    def auto_load_skills(self, cwd=None):
        return self._registry.auto_load_skills(cwd)

    def invalidate(self) -> None:
        self._registry.invalidate()


@plugin("skills", inject=["config"], provide=["skills"])
def apply(ctx):
    return SkillsService(ctx)
