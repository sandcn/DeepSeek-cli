"""补全提供者插件 — 提供 ``ctx.completion_providers``。

「一切皆插件」：终端补全提供者从 ``CompletionEngine`` 的硬编码分支上移为内核
服务 + 可替换注册表。内置提供者由清单中的独立条目
（``completion_provider``，经 ``src.plugins.completion_provider_entries``）
注册——可按 Profile/Patch 禁用或替换；外部插件可经
``ctx.completion_providers.register(spec)`` 注册自定义提供者。

补全消费经 ``src.tui._completion_engine`` 的注册表解析；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class CompletionProvidersService(Service):
    """补全提供者服务 — 占据 ``ctx.completion_providers``。"""

    provide = "completion_providers"
    name = "completion_providers"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui._completion_providers import (
            disable_builtin_providers,
            set_managed_builtin_providers,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_completion_providers") or ()
        if managed:
            undo_managed = set_managed_builtin_providers(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_completion_providers") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_providers(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置提供者 id（含被接管/禁用的）。"""
        from ..tui._completion_providers import builtin_provider_ids

        return list(builtin_provider_ids())

    def active(self) -> list:
        """当前生效的内置提供者 id（按 order）。"""
        from ..tui._completion_providers import active_provider_ids

        return list(active_provider_ids())

    def managed(self) -> list:
        from ..tui._completion_providers import managed_provider_ids

        return list(managed_provider_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, spec):
        """注册扩展提供者（注册即副作用，卸载时自动撤销）。"""
        from ..tui._completion_providers import register_provider

        undo = register_provider(spec)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, spec_id: str) -> bool:
        from ..tui._completion_providers import unregister_provider

        return unregister_provider(spec_id)


@plugin("completion_providers", inject=["config"], provide=["completion_providers"])
def apply(ctx):
    return CompletionProvidersService(ctx, ctx.config)


__all__ = ["CompletionProvidersService", "apply"]
