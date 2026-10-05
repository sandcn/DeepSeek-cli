"""表现层数据插件 — 提供 ``ctx.presentation_data``。

「一切皆插件」：渲染/TUI 的纯数据表从各模块硬编码字典上移为内核服务 +
可替换注册表。内置表由清单中的独立条目
（``presentation_data``，经 ``src.plugins.presentation_data_entries``）注册——
可按 Profile/Patch 覆盖、禁用或替换；外部插件可经
``ctx.presentation_data.register(table)`` 注册自定义数据表。

消费经 ``src.presentation_data`` 的实时查询；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class PresentationDataService(Service):
    """表现层数据服务 — 占据 ``ctx.presentation_data``。"""

    provide = "presentation_data"
    name = "presentation_data"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..presentation_data import (
            disable_builtin_data,
            set_managed_builtin_data,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_presentation_data") or ()
        if managed:
            undo_managed = set_managed_builtin_data(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_presentation_data") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_data(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置数据表 id（含被接管/禁用的）。"""
        from ..presentation_data import builtin_data_ids

        return list(builtin_data_ids())

    def active(self) -> dict:
        """当前生效的内置数据表（``id → 规格 dict``）。"""
        from ..presentation_data import active_data_tables

        return {spec_id: spec.to_dict() for spec_id, spec in active_data_tables().items()}

    def managed(self) -> list:
        from ..presentation_data import managed_data_ids

        return list(managed_data_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def table(self, name: str, default=None):
        """按表名取当前生效的数据。"""
        from ..presentation_data import data_table

        return data_table(name, default)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, table):
        """注册扩展数据表（注册即副作用，卸载时自动撤销）。"""
        from ..presentation_data import register_data_table

        undo = register_data_table(table)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, spec_id: str) -> bool:
        from ..presentation_data import unregister_data_table

        return unregister_data_table(spec_id)


@plugin("presentation_data", inject=["config"], provide=["presentation_data"])
def apply(ctx):
    return PresentationDataService(ctx, ctx.config)


__all__ = ["PresentationDataService", "apply"]
