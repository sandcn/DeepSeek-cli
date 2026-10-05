"""事件类型插件 — 提供 ``ctx.event_types``。

「一切皆插件」：事件类型（核心事件类型字符串常量 + 显示事件类型类）从各模块的
硬编码字面量/类元组上移为内核服务 + 可替换注册表。内置事件类型由清单中的独立
条目（``event_type``，经 ``src.plugins.event_type_entries``）注册——可按
Profile/Patch 覆盖、禁用或替换；外部插件可经
``ctx.event_types.register(composite_id, value)`` 注册自定义事件类型。

消费经 ``src.core.events.type_registry`` 的实时查询（``ALL_EVENT_TYPES`` /
``event_types`` 常量属性）；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class EventTypesService(Service):
    """事件类型服务 — 占据 ``ctx.event_types``。"""

    provide = "event_types"
    name = "event_types"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..core.events.type_registry import set_managed_builtin_events

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_event_types") or ()
        self._managed = list(managed)
        if managed:
            undo_managed = set_managed_builtin_events(managed)
            ctx.effect(lambda: undo_managed)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置事件类型复合 id（含被接管/禁用的）。"""
        from ..core.events.type_registry import builtin_composite_ids

        return list(builtin_composite_ids())

    def active(self) -> list:
        """当前生效的事件类型复合 id。"""
        from ..core.events.type_registry import registered_composite_ids

        return list(registered_composite_ids())

    def managed(self) -> list:
        return sorted(self._managed)

    def domain(self, name: str) -> dict:
        """某域当前生效的事件类型（``名称 → 值``）。"""
        from ..core.events.type_registry import active_events

        return active_events(name)

    def value(self, composite: str, default=None):
        from ..core.events.type_registry import event_by_composite

        return event_by_composite(composite, default)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, composite: str, value):
        """注册扩展事件类型（注册即副作用，卸载时自动撤销）。"""
        from ..core.events.type_registry import register_event

        undo = register_event(composite, value)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, composite: str) -> bool:
        from ..core.events.type_registry import unregister_event

        return unregister_event(composite)


@plugin("event_types", inject=["config"], provide=["event_types"])
def apply(ctx):
    return EventTypesService(ctx, ctx.config)


__all__ = ["EventTypesService", "apply"]
