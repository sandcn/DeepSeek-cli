"""Web 抓取插件 — 提供 ``ctx.web_fetch``。

「一切皆插件」：``web_fetch`` 工具的抓取提供者（fetch provider）从直接调用
``page_fetcher`` 上移为内核服务 + 可替换注册表。内置 ``http`` 提供者由清单
中的独立条目（``web_fetch_provider``，经 ``src.plugins.web_fetch_providers``）
声明，可按 Profile/Patch 禁用、覆盖或替换；外部插件可经
``ctx.web_fetch.register_provider(name, factory)`` 注册自己的抓取提供者。

工具侧经 ``src.tools.fetch_provider_registry.active_fetch_provider`` 解析本
服务（内核缺失时回退进程级默认注册表，保持单元测试与独立调用兼容）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class WebFetchService(Service):
    """Web 抓取服务 — 占据 ``ctx.web_fetch``。"""

    provide = "web_fetch"
    name = "web_fetch"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tools.fetch_provider_registry import DEFAULT_FETCH_PROVIDER

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_fetch_providers") or ()
        if managed:
            from ..tools.fetch_provider_registry import set_managed_builtin_fetch_providers

            undo_managed = set_managed_builtin_fetch_providers(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_fetch_providers") or ()
        self._disabled = list(disabled)
        if disabled:
            from ..tools.fetch_provider_registry import disable_builtin_fetch_providers

            undo_disabled = disable_builtin_fetch_providers(disabled)
            ctx.effect(lambda: undo_disabled)
        self._default = cfg.get("provider") or DEFAULT_FETCH_PROVIDER

    # ── 自省 ─────────────────────────────────────────────

    def provider_names(self) -> list:
        from ..tools.fetch_provider_registry import (
            builtin_fetch_provider_factories,
            fetch_provider_factories,
        )

        return sorted(set(builtin_fetch_provider_factories()) | set(fetch_provider_factories()))

    def builtin_provider_ids(self) -> list:
        from ..tools.fetch_provider_registry import builtin_fetch_provider_ids

        return list(builtin_fetch_provider_ids())

    def managed(self) -> list:
        from ..tools.fetch_provider_registry import managed_fetch_provider_ids

        return list(managed_fetch_provider_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def default_provider(self) -> str:
        return self._default

    # ── 解析 / 注册 ──────────────────────────────────────

    def resolve_provider(self, name=None):
        from ..tools.fetch_provider_registry import resolve_fetch_provider

        return resolve_fetch_provider(name or self._default)

    def register_provider(self, name: str, factory) -> object:
        from ..tools.fetch_provider_registry import register_fetch_provider

        undo = register_fetch_provider(name, factory)
        self.ctx.effect(lambda: undo)
        return undo


@plugin("web_fetch", inject=["config"], provide=["web_fetch"])
def apply(ctx):
    return WebFetchService(ctx, ctx.config)


__all__ = ["WebFetchService", "apply"]
