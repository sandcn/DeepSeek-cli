"""键位绑定插件 — 提供 ``ctx.keybindings``。

「一切皆插件」：TUI Ctrl 组合键绑定从 ``InputDispatcher`` 的硬编码分支上移为
内核服务 + 可替换注册表。内置绑定由清单中的独立条目
（``keybinding``，经 ``src.plugins.keybinding_entries``）注册——可按
Profile/Patch 禁用、覆盖（改键/换 action）或替换；外部插件可经
``ctx.keybindings.register(spec)`` 注册自定义绑定。

输入分发经 ``src.tui._keybindings.resolve_binding`` 读取生效绑定；自省经本
服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class KeybindingsService(Service):
    """键位绑定服务 — 占据 ``ctx.keybindings``。"""

    provide = "keybindings"
    name = "keybindings"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tui._keybindings import (
            disable_builtin_keybindings,
            set_managed_builtin_keybindings,
        )

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_keybindings") or ()
        if managed:
            undo_managed = set_managed_builtin_keybindings(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_keybindings") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_keybindings(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 自省 ─────────────────────────────────────────────

    def ids(self) -> list:
        """全部内置绑定 id（含被接管/禁用的）。"""
        from ..tui._keybindings import builtin_keybinding_ids

        return list(builtin_keybinding_ids())

    def active(self) -> dict:
        """当前生效的内置绑定（``id → 规格 dict``）。"""
        from ..tui._keybindings import active_keybindings

        return {spec_id: spec.to_dict() for spec_id, spec in active_keybindings().items()}

    def managed(self) -> list:
        from ..tui._keybindings import managed_keybinding_ids

        return list(managed_keybinding_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    def resolve(self, key: str):
        """解析按键对应的分发动作（无匹配返回 None）。"""
        from ..tui._keybindings import resolve_binding

        return resolve_binding(key)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, spec):
        """注册扩展绑定（注册即副作用，卸载时自动撤销）。"""
        from ..tui._keybindings import register_keybinding

        undo = register_keybinding(spec)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, spec_id: str) -> bool:
        from ..tui._keybindings import unregister_keybinding

        return unregister_keybinding(spec_id)


@plugin("keybindings", inject=["config"], provide=["keybindings"])
def apply(ctx):
    return KeybindingsService(ctx, ctx.config)


__all__ = ["KeybindingsService", "apply"]
