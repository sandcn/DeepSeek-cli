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
        from ..mcp.manager import McpManager

        self._manager = McpManager(registry=self._tool_registry())
        self._active = False
        ctx.effect(lambda: self._aclose)

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
    return McpService(ctx)
