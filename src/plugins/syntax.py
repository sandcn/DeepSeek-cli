"""语法高亮插件 — 提供 ``ctx.syntax``。

「一切皆插件」：``CodeBlock`` 高亮语言表从 ``_syntax`` 的硬编码字典上移为内核
服务 + 可替换注册表。内置语言由清单中的独立条目
（``syntax_language``，经 ``src.plugins.syntax_entries``）注册——可按
Profile/Patch 覆盖、禁用或替换；外部插件可经
``ctx.syntax.register(spec)`` 注册自定义语言。

渲染消费经 ``src.tui.ink.widgets._syntax`` 的实时查询；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class SyntaxService(Service):
    """语法高亮语言服务 — 占据 ``ctx.syntax``。"""

    provide = "syntax"
    name = "syntax"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui.ink.widgets._syntax_registry import (
            disable_builtin_languages,
            set_managed_builtin_languages,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_syntax_languages") or ()
        if managed:
            undo_managed = set_managed_builtin_languages(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_syntax_languages") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_languages(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置语言 id（含被接管/禁用的）。"""
        from ..tui.ink.widgets._syntax_registry import builtin_language_ids

        return list(builtin_language_ids())

    def active(self) -> list:
        """当前生效（有高亮支持）的语言 id。"""
        from ..tui.ink.widgets._syntax_registry import supported_language_ids

        return list(supported_language_ids())

    def managed(self) -> list:
        from ..tui.ink.widgets._syntax_registry import managed_language_ids

        return list(managed_language_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def normalize(self, language) -> str:
        from ..tui.ink.widgets._syntax_registry import normalize_language

        return normalize_language(language)

    def keywords(self, lang_id: str) -> set:
        from ..tui.ink.widgets._syntax_registry import language_keywords

        return language_keywords(lang_id)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, spec):
        """注册扩展语言（注册即副作用，卸载时自动撤销）。"""
        from ..tui.ink.widgets._syntax_registry import register_language

        undo = register_language(spec)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, lang_id: str) -> bool:
        from ..tui.ink.widgets._syntax_registry import unregister_language

        return unregister_language(lang_id)


@plugin("syntax", inject=["config"], provide=["syntax"])
def apply(ctx):
    return SyntaxService(ctx, ctx.config)


__all__ = ["SyntaxService", "apply"]
