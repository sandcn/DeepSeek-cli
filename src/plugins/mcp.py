"""MCP 插件 — 提供 ``ctx.mcp``。

外部工具接入：根据配置连接 MCP server、发现工具并注册到内核的
``ctx.tools`` 注册表；卸载时关闭连接并注销动态工具。

「一切皆插件」：MCP 管理器不再是游离进程级单例——本服务**独占**一个
:class:`McpManager` 实例（``ctx.mcp.manager``），``McpManager.default()``
内核优先返回该实例；内核缺失（单元测试、独立调用）时才回退进程级单例。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class McpService(Service):
    """MCP 服务 — 占据 ``ctx.mcp``。"""

    provide = "mcp"
    name = "mcp"
    inject = ("tools", "config")

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_mcp_transports") or ()
        if managed:
            from ..mcp.transport_registry import set_managed_builtin_mcp_transports

            undo_managed = set_managed_builtin_mcp_transports(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_mcp_transports") or ()
        if disabled:
            from ..mcp.transport_registry import disable_builtin_mcp_transports

            undo_disabled = disable_builtin_mcp_transports(disabled)
            ctx.effect(lambda: undo_disabled)
        from ..mcp.manager import McpManager

        self._manager = McpManager(registry=self._tool_registry())
        self._active = False
        ctx.effect(lambda: self._aclose)

    def transports(self) -> list:
        """当前生效的 MCP 传输 id 列表（自省）。"""
        from ..mcp.transport_registry import (
            builtin_mcp_transport_factories,
            mcp_transport_factories,
        )

        return sorted(set(builtin_mcp_transport_factories()) | set(mcp_transport_factories()))

    def _tool_registry(self):
        if self.ctx.has("tools"):
            return self.ctx.tools.registry
        from ..tools.registry import ToolRegistry

        return ToolRegistry.default()

    @property
    def manager(self):
        """本服务独占的 McpManager 实例（内核真源，非进程级单例）。"""
        return self._manager

    async def setup_mcp(self, servers=None):
        """连接配置的 MCP server 并注册其工具（未配置时零开销）。"""
        await self._manager.initialize(servers=servers, registry=self._tool_registry())
        self._active = True

    async def shutdown_mcp(self) -> None:
        """关闭全部 MCP 连接并清理注册。"""
        await self._manager.close()
        self._active = False

    async def call_tool(self, server, tool, arguments=None):
        """路由一次 MCP 工具调用（供工具执行钩子与外部插件使用）。"""
        return await self._manager.call_tool(server, tool, arguments)

    async def _aclose(self) -> None:
        if self._active:
            try:
                await self.shutdown_mcp()
            except Exception:
                self._active = False

    def status(self) -> list:
        return self._manager.status()

    def prompt_section(self, agent_type=None) -> str:
        return self._manager.build_prompt_section(agent_type)

    @property
    def active(self) -> bool:
        return self._active


@plugin("mcp", inject=["tools", "config"], provide=["mcp"])
def apply(ctx):
    return McpService(ctx, ctx.config)
