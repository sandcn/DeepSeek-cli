"""host 组件插件 — 提供 ``ctx.hosts``。

「一切皆插件」：内置 host（``static-lines``）的 measure/paint 从模块导入期
副作用上移为内核服务 + 可替换注册表。内置 host 由清单中的独立条目
（``host``，经 ``src.plugins.host_entries``）注册——可按 Profile/Patch 禁用或
替换；外部插件可经 ``ctx.hosts.register(tag, measure, paint)`` 注册自定义 host。

布局/绘制经 ``src.tui.ink.registry.get_host`` 查询生效 host；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class HostsService(Service):
    """host 组件服务 — 占据 ``ctx.hosts``。"""

    provide = "hosts"
    name = "hosts"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui.ink.registry import (
            disable_builtin_hosts,
            set_managed_builtin_hosts,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_hosts") or ()
        if managed:
            undo_managed = set_managed_builtin_hosts(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_hosts") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_hosts(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置 host tag（含被接管/禁用的）。"""
        from ..tui.ink.registry import builtin_host_ids

        return list(builtin_host_ids())

    def active(self) -> list:
        """当前生效的内置 host tag。"""
        from ..tui.ink.registry import active_hosts

        return sorted(active_hosts())

    def managed(self) -> list:
        from ..tui.ink.registry import managed_host_ids

        return list(managed_host_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, tag: str, measure, paint):
        """注册自定义 host（注册即副作用，卸载时自动撤销）。"""
        from ..tui.ink.registry import register_host, unregister_host

        register_host(tag, measure, paint)

        def _undo() -> None:
            unregister_host(tag)

        self.ctx.effect(_undo)
        return _undo

    def unregister(self, tag: str) -> None:
        from ..tui.ink.registry import unregister_host

        unregister_host(tag)


@plugin("hosts", inject=["config"], provide=["hosts"])
def apply(ctx):
    return HostsService(ctx, ctx.config)


__all__ = ["HostsService", "apply"]
