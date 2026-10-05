"""命名样式插件 — 提供 ``ctx.named_styles``。

「一切皆插件」：内置命名样式集从 ``tui.core.style`` 的模块级硬编码字典上移为
内核服务 + 可替换注册表。内置样式由清单中的独立条目（``named_style``，经
``src.plugins.style_entries``）注册——可按 Profile/Patch 覆盖、禁用或替换；
外部插件可经 ``ctx.named_styles.register(name, style)`` 注册自定义命名样式。

消费经 ``StyleSheet`` / ``src.tui.core.style`` 访问器的实时查询；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class NamedStylesService(Service):
    """命名样式服务 — 占据 ``ctx.named_styles``。"""

    provide = "named_styles"
    name = "named_styles"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui.core.style import set_managed_builtin_styles

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_named_styles") or ()
        self._managed = list(managed)
        if managed:
            undo_managed = set_managed_builtin_styles(managed)
            ctx.effect(lambda: undo_managed)

    # ── 自省 ─────────────────────────────────────────────

    def names(self) -> list:
        """全部内置命名样式名（含被接管/禁用的）。"""
        from ..tui.core.style import builtin_style_names

        return list(builtin_style_names())

    def active(self) -> list:
        """当前生效的内置命名样式名。"""
        from ..tui.core.style import active_style_names

        return list(active_style_names())

    def managed(self) -> list:
        return sorted(self._managed)

    def style(self, name: str, default=None):
        """按名称取当前生效样式（缺席返回 ``default``）。"""
        from ..tui.core.style import active_style

        value = active_style(name)
        return default if value is None else value

    # ── 注册 ─────────────────────────────────────────────

    def register(self, name: str, style):
        """注册扩展命名样式（注册即副作用，卸载时自动撤销）。"""
        from ..tui.core.style import register_style_extension

        undo = register_style_extension(name, style)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, name: str) -> bool:
        from ..tui.core.style import unregister_style_extension

        return unregister_style_extension(name)


@plugin("named_styles", inject=["config"], provide=["named_styles"])
def apply(ctx):
    return NamedStylesService(ctx, ctx.config)


__all__ = ["NamedStylesService", "apply"]
