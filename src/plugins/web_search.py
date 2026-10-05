"""Web 搜索插件 — 提供 ``ctx.web_search``。

「一切皆插件」：``web_search`` 工具的搜索提供者（provider）从工具类硬编码
上移为内核服务 + 可替换注册表。内置 ``deepseek`` 提供者由清单中的独立条目
（``web_search_provider``，经 ``src.plugins.web_search_providers``）声明，
可按 Profile/Patch 禁用、覆盖或替换；外部插件可经
``ctx.web_search.register_provider(name, factory)`` 注册自己的提供者
（对齐 dsh 的 ``dsh-web-search-*`` 拆包）。

工具侧经 ``src.tools.search_provider_registry.active_search_provider`` 解析
本服务（内核缺失时回退进程级默认注册表，保持单元测试与独立调用兼容）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class WebSearchService(Service):
    """Web 搜索服务 — 占据 ``ctx.web_search``。"""

    provide = "web_search"
    name = "web_search"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tools.search_provider_registry import DEFAULT_SEARCH_PROVIDER

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_search_providers") or ()
        if managed:
            from ..tools.search_provider_registry import set_managed_builtin_search_providers

            undo_managed = set_managed_builtin_search_providers(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_search_providers") or ()
        self._disabled = list(disabled)
        if disabled:
            from ..tools.search_provider_registry import disable_builtin_search_providers

            undo_disabled = disable_builtin_search_providers(disabled)
            ctx.effect(lambda: undo_disabled)
        self._default = cfg.get("provider") or DEFAULT_SEARCH_PROVIDER

    # ── 自省 ─────────────────────────────────────────────

    def provider_names(self) -> list:
        """当前生效的提供者名（生效内置 + 扩展）。"""
        from ..tools.search_provider_registry import (
            builtin_search_provider_factories,
            search_provider_factories,
        )

        return sorted(set(builtin_search_provider_factories()) | set(search_provider_factories()))

    def builtin_provider_ids(self) -> list:
        """全部内置提供者 id（含被接管/禁用的）。"""
        from ..tools.search_provider_registry import builtin_search_provider_ids

        return list(builtin_search_provider_ids())

    def managed(self) -> list:
        from ..tools.search_provider_registry import managed_search_provider_ids

        return list(managed_search_provider_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def default_provider(self) -> str:
        return self._default

    # ── 解析 / 注册 ──────────────────────────────────────

    def resolve_provider(self, name=None):
        """按名解析提供者实例（None/空用本服务默认名）。"""
        from ..tools.search_provider_registry import resolve_search_provider

        return resolve_search_provider(name or self._default)

    def register_provider(self, name: str, factory) -> object:
        """注册扩展搜索提供者（注册即副作用，卸载时自动撤销）。"""
        from ..tools.search_provider_registry import register_search_provider

        undo = register_search_provider(name, factory)
        self.ctx.effect(lambda: undo)
        return undo


@plugin("web_search", inject=["config"], provide=["web_search"])
def apply(ctx):
    return WebSearchService(ctx, ctx.config)


__all__ = ["WebSearchService", "apply"]
