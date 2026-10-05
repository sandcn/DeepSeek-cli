"""工具常量插件 — 提供 ``ctx.tool_consts``。

「一切皆插件」：工具运行期常量从各模块的模块级字面量上移为内核服务 + 可替换
注册表。内置常量由清单中的独立条目（``tool_const_entry``，经
``src.plugins.tool_const_entries``）注册——可按 Profile/Patch 覆盖、禁用或
替换；外部插件可经 ``ctx.tool_consts.register(name, value)`` 注册自定义常量。

消费经 ``src.tools.const_registry.const`` / ``src.tools._constants`` 访问器的
实时查询；自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ToolConstsService(Service):
    """工具常量服务 — 占据 ``ctx.tool_consts``。"""

    provide = "tool_consts"
    name = "tool_consts"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tools.const_registry import set_managed_builtin_constants

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_tool_consts") or ()
        self._managed = list(managed)
        if managed:
            undo_managed = set_managed_builtin_constants(managed)
            ctx.effect(lambda: undo_managed)

    # ── 自省 ─────────────────────────────────────────────

    def names(self) -> list:
        """全部内置常量名（含被接管/禁用的）。"""
        from ..tools.const_registry import builtin_constant_names

        return list(builtin_constant_names())

    def active(self) -> dict:
        """当前生效的常量（``名称 → 值``）。"""
        from ..tools.const_registry import active_constants

        return active_constants()

    def managed(self) -> list:
        return sorted(self._managed)

    def constant(self, name: str, default=None):
        """按名称取当前生效常量值（缺席返回 ``default``）。"""
        from ..tools.const_registry import const

        return const(name, default)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, name: str, value):
        """注册扩展常量（注册即副作用，卸载时自动撤销）。"""
        from ..tools.const_registry import register_constant

        undo = register_constant(name, value)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, name: str) -> bool:
        from ..tools.const_registry import unregister_constant

        return unregister_constant(name)


@plugin("tool_consts", inject=["config"], provide=["tool_consts"])
def apply(ctx):
    return ToolConstsService(ctx, ctx.config)


__all__ = ["ToolConstsService", "apply"]
