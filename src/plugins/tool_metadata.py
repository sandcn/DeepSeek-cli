"""工具元数据插件 — 提供 ``ctx.tool_metadata``。

「一切皆插件」：工具元数据（``parallel_safe`` / ``category`` / ``priority`` 等）
从各工具类的 ``@tool_metadata`` 装饰器上移为内核服务 + 可替换注册表。内置元数据
由清单中的独立条目（``tool_metadata_entry``，经
``src.plugins.tool_metadata_entries``）注册——可按 Profile/Patch 覆盖、禁用或
替换；外部插件可经 ``ctx.tool_metadata.register(name, metadata)`` 注册运行时
（MCP / 动态工具）元数据。

消费经 ``src.tools.metadata_registry`` 的实时查询（``Func.get_metadata``）；
自省经本服务。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ToolMetadataService(Service):
    """工具元数据服务 — 占据 ``ctx.tool_metadata``。"""

    provide = "tool_metadata"
    name = "tool_metadata"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tools.metadata_registry import set_managed_builtin_metadata

        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_tool_metadata") or ()
        self._managed = list(managed)
        if managed:
            undo_managed = set_managed_builtin_metadata(managed)
            ctx.effect(lambda: undo_managed)

    # ── 自省 ─────────────────────────────────────────────

    def names(self) -> list:
        """全部内置工具名（含被接管/禁用的）。"""
        from ..tools.metadata_registry import builtin_tool_names

        return list(builtin_tool_names())

    def active(self) -> dict:
        """当前生效的工具元数据（``工具名 → 元数据 dict``）。"""
        from ..tools.metadata_registry import active_metadata

        return active_metadata()

    def managed(self) -> list:
        return sorted(self._managed)

    def metadata(self, name: str, default=None):
        """按工具名取当前生效元数据（缺席返回 ``default``）。"""
        from ..tools.metadata_registry import metadata_for

        value = metadata_for(name)
        return default if value is None else value

    # ── 注册 ─────────────────────────────────────────────

    def register(self, name: str, metadata: dict):
        """注册扩展元数据（注册即副作用，卸载时自动撤销）。"""
        from ..tools.metadata_registry import register_metadata

        undo = register_metadata(name, metadata)
        self.ctx.effect(lambda: undo)
        return undo

    def unregister(self, name: str) -> bool:
        from ..tools.metadata_registry import unregister_metadata

        return unregister_metadata(name)


@plugin("tool_metadata", inject=["config"], provide=["tool_metadata"])
def apply(ctx):
    return ToolMetadataService(ctx, ctx.config)


__all__ = ["ToolMetadataService", "apply"]
