"""MCP 插件 — 提供 ``ctx.mcp``。

外部工具接入：根据配置连接 MCP server、发现工具并注册到内核的
``ctx.tools`` 注册表；卸载时关闭连接并注销动态工具。
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
        self._active = False
        ctx.effect(lambda: self._aclose)

    async def setup_mcp(self, servers=None):
        """连接配置的 MCP server 并注册其工具（未配置时零开销）。"""
        from ..mcp import setup_mcp

        registry = None
        if self.ctx.has("tools"):
            registry = self.ctx.tools.registry
        await setup_mcp(servers=servers, registry=registry)
        self._active = True

    async def shutdown_mcp(self) -> None:
        """关闭全部 MCP 连接并清理注册。"""
        from ..mcp import shutdown_mcp

        await shutdown_mcp()
        self._active = False

    async def _aclose(self) -> None:
        if self._active:
            try:
                await self.shutdown_mcp()
            except Exception:
                self._active = False

    def status(self) -> list:
        from ..mcp import get_mcp_status

        return get_mcp_status()

    def prompt_section(self, agent_type=None) -> str:
        from ..mcp import get_mcp_prompt_section

        return get_mcp_prompt_section(agent_type)

    @property
    def active(self) -> bool:
        return self._active


@plugin("mcp", inject=["tools", "config"], provide=["mcp"])
def apply(ctx):
    return McpService(ctx)
