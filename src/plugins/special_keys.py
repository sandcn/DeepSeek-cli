"""特殊键处理器插件 — 提供 ``ctx.special_keys``。

「一切皆插件」：``make_special_key_callback`` 的 action 处理器从硬编码 if/elif
上移为内核服务 + 可替换注册表。内置处理器由清单中的独立条目
（``special_key``，经 ``src.plugins.special_key_entries``）注册——可按
Profile/Patch 禁用、覆盖或替换；外部插件可经
``ctx.special_keys.register(action, factory)`` 注册自定义处理器。

回调装配经 ``src.app_loop._special_handlers.build_special_key_handlers`` 读取
生效处理器；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class SpecialKeysService(Service):
    """特殊键处理器服务 — 占据 ``ctx.special_keys``。"""

    provide = "special_keys"
    name = "special_keys"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..app_loop._special_handlers import (
            disable_builtin_special_keys,
            set_managed_builtin_special_keys,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_special_keys") or ()
        if managed:
            undo_managed = set_managed_builtin_special_keys(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_special_keys") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_special_keys(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置处理器 id（含被接管/禁用的）。"""
        from ..app_loop._special_handlers import builtin_special_key_ids

        return list(builtin_special_key_ids())

    def active(self) -> list:
        """当前生效的内置处理器 id。"""
        from ..app_loop._special_handlers import active_special_key_factories

        return sorted(active_special_key_factories())

    def managed(self) -> list:
        from ..app_loop._special_handlers import managed_special_key_ids

        return list(managed_special_key_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def resolve(self, action: str):
        """解析某 action 的处理器工厂（无匹配返回 None）。"""
        from ..app_loop._special_handlers import resolve_special_key_factory

        return resolve_special_key_factory(action)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, action: str, factory):
        """注册扩展处理器（注册即副作用，卸载时自动撤销）。"""
        from ..app_loop._special_handlers import register_special_key

        undo = register_special_key(action, factory)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, action: str) -> bool:
        from ..app_loop._special_handlers import unregister_special_key

        return unregister_special_key(action)


@plugin("special_keys", inject=["config"], provide=["special_keys"])
def apply(ctx):
    return SpecialKeysService(ctx, ctx.config)


__all__ = ["SpecialKeysService", "apply"]
