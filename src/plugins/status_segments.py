"""状态栏段插件 — 提供 ``ctx.status_segments``。

「一切皆插件」：StatusBar 的信息段从 ``status_bar._build_status_runs`` 的硬编码
上移为内核服务 + 可替换注册表。内置段由清单中的独立条目
（``status_segment``，经 ``src.plugins.status_segment_entries``）注册——可按
Profile/Patch 禁用或替换；外部插件可经
``ctx.status_segments.register(spec)`` 注册自定义段。

渲染消费经 ``src.tui.app._status_segments`` 的注册表解析；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class StatusSegmentsService(Service):
    """状态栏段服务 — 占据 ``ctx.status_segments``。"""

    provide = "status_segments"
    name = "status_segments"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui.app._status_segments import (
            disable_builtin_segments,
            set_managed_builtin_segments,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_status_segments") or ()
        if managed:
            undo_managed = set_managed_builtin_segments(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_status_segments") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_segments(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置段 id（含被接管/禁用的）。"""
        from ..tui.app._status_segments import builtin_segment_ids

        return list(builtin_segment_ids())

    def active(self) -> list:
        """当前生效的内置段 id（按 order）。"""
        from ..tui.app._status_segments import active_segment_ids

        return list(active_segment_ids())

    def managed(self) -> list:
        from ..tui.app._status_segments import managed_segment_ids

        return list(managed_segment_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, spec):
        """注册扩展段（注册即副作用，卸载时自动撤销）。"""
        from ..tui.app._status_segments import register_segment

        undo = register_segment(spec)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, spec_id: str) -> bool:
        from ..tui.app._status_segments import unregister_segment

        return unregister_segment(spec_id)


@plugin("status_segments", inject=["config"], provide=["status_segments"])
def apply(ctx):
    return StatusSegmentsService(ctx, ctx.config)


__all__ = ["StatusSegmentsService", "apply"]
