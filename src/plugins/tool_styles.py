"""工具表现插件 — 提供 ``ctx.tool_styles``。

「一切皆插件」：工具/类别/Agent 类型表现映射从 ``src.tui._tool_icons`` 的硬编码
字典上移为内核服务 + 可替换注册表。内置表现由清单中的独立条目
（``tool_style``，经 ``src.plugins.tool_style_entries``）注册——可按
Profile/Patch 覆盖、禁用或替换；外部插件可经
``ctx.tool_styles.register(spec)`` 注册自定义表现。

渲染消费经 ``src.tui._tool_styles`` 的实时查询函数；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ToolStylesService(Service):
    """工具表现服务 — 占据 ``ctx.tool_styles``。"""

    provide = "tool_styles"
    name = "tool_styles"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui._tool_styles import (
            disable_builtin_presentations,
            set_managed_builtin_presentations,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_tool_styles") or ()
        if managed:
            undo_managed = set_managed_builtin_presentations(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_tool_styles") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_presentations(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置表现条目 id（含被接管/禁用的）。"""
        from ..tui._tool_styles import builtin_presentation_ids

        return list(builtin_presentation_ids())

    def active(self) -> dict:
        """当前生效的内置表现（``id → 规格 dict``）。"""
        from ..tui._tool_styles import active_presentations

        return {spec_id: spec.to_dict() for spec_id, spec in active_presentations().items()}

    def managed(self) -> list:
        from ..tui._tool_styles import managed_presentation_ids

        return list(managed_presentation_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def tool_category(self, tool_name: str) -> str:
        from ..tui._tool_styles import tool_category

        return tool_category(tool_name)

    def category_style(self, category: str):
        from ..tui._tool_styles import category_style

        return category_style(category)

    def agent_type_abbrev(self, agent_type: str) -> str:
        from ..tui._tool_styles import agent_type_abbrev

        return agent_type_abbrev(agent_type)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, spec):
        """注册扩展表现条目（注册即副作用，卸载时自动撤销）。"""
        from ..tui._tool_styles import register_presentation

        undo = register_presentation(spec)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, spec_id: str) -> bool:
        from ..tui._tool_styles import unregister_presentation

        return unregister_presentation(spec_id)


@plugin("tool_styles", inject=["config"], provide=["tool_styles"])
def apply(ctx):
    return ToolStylesService(ctx, ctx.config)


__all__ = ["ToolStylesService", "apply"]
