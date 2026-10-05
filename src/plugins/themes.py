"""主题插件 — 提供 ``ctx.themes``。

「一切皆插件」：内置主题（dark/light/high-contrast）不再是
``tui.core._theme.ThemeRegistry`` 的类属性硬编码，而是由清单中的独立条目
（``theme``，经 ``src.plugins.theme_entries``）注册进主题注册表——可按
Profile/Patch 禁用、覆盖或替换；外部插件可经
``ctx.themes.register(name, factory)`` 注册自定义主题。

组件经 ``tui.core._theme.get_active_palette()`` 读取活动主题；列表/校验
（``/theme`` 命令）经本服务自省。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ThemesService(Service):
    """主题服务 — 占据 ``ctx.themes``。"""

    provide = "themes"
    name = "themes"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui.core._theme import (
            disable_builtin_themes,
            set_managed_builtin_themes,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_themes") or ()
        if managed:
            undo_managed = set_managed_builtin_themes(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_themes") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_themes(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def names(self) -> list:
        """当前生效的主题名（内置 + 扩展）。"""
        from ..tui.core._theme import ThemeRegistry

        return list(ThemeRegistry.names())

    def builtin_names(self) -> list:
        """全部内置主题名（含被接管/禁用的）。"""
        from ..tui.core._theme import builtin_theme_names

        return list(builtin_theme_names())

    def managed(self) -> list:
        from ..tui.core._theme import managed_theme_names

        return list(managed_theme_names())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def palette(self, name: str):
        """按名解析调色板（未知返回 None）。"""
        from ..tui.core._theme import ThemeRegistry

        return ThemeRegistry.get(name)

    def active(self):
        """当前活动调色板（读 config theme）。"""
        from ..tui.core._theme import get_active_palette

        return get_active_palette()

    def active_name(self) -> str:
        return self.ctx.consume("config").get("theme", "dark") or "dark"

    # ── 注册 ─────────────────────────────────────────────

    def register(self, name: str, factory) -> object:
        """注册扩展主题（注册即副作用，卸载时自动撤销）。"""
        from ..tui.core._theme import register_theme

        undo = register_theme(name, factory)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, name: str) -> bool:
        from ..tui.core._theme import unregister_theme

        return unregister_theme(name)


@plugin("themes", inject=["config"], provide=["themes"])
def apply(ctx):
    return ThemesService(ctx, ctx.config)


__all__ = ["ThemesService", "apply"]
