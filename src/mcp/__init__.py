"""MCP（Model Context Protocol）外部工具接入子系统。

配置示例（``~/.chat_config/chatrc.json``）::

    "mcp_servers": [
      {
        "name": "filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "agents": ["execute"]
      },
      {
        "name": "remote",
        "transport": "http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"}
      }
    ]

应用启动时调用 ``setup_mcp()`` 建立连接、发现工具并注册为内置工具；
退出时调用 ``shutdown_mcp()`` 关闭连接并清理注册。未配置 ``mcp_servers``
时本子系统零开销（不建立任何连接、不注册任何工具）。

注意：``headers`` 中的凭据按明文配置处理，请勿把真实 token 提交进版本库。
"""

from .client import McpClient, McpToolDef, McpToolResult
from .config import (
    McpServerConfig,
    load_mcp_servers,
    parse_mcp_servers,
    parse_server,
    validate_mcp_servers,
)
from .errors import McpConfigError, McpError, McpProtocolError, McpTransportError
from .manager import (
    McpManager,
    get_mcp_prompt_section,
    get_mcp_status,
    register_tool_policy,
    setup_mcp,
    shutdown_mcp,
    unregister_tool_policy,
)
from .protocol import PROTOCOL_VERSION
from .tool import (
    MCP_TOOL_PREFIX,
    build_mcp_tool_class,
    is_mcp_tool,
    mcp_tool_name,
    normalize_input_schema,
)

__all__ = [
    "McpClient",
    "McpToolDef",
    "McpToolResult",
    "McpServerConfig",
    "load_mcp_servers",
    "parse_mcp_servers",
    "parse_server",
    "validate_mcp_servers",
    "McpError",
    "McpConfigError",
    "McpProtocolError",
    "McpTransportError",
    "McpManager",
    "setup_mcp",
    "shutdown_mcp",
    "get_mcp_prompt_section",
    "get_mcp_status",
    "register_tool_policy",
    "unregister_tool_policy",
    "PROTOCOL_VERSION",
    "MCP_TOOL_PREFIX",
    "build_mcp_tool_class",
    "is_mcp_tool",
    "mcp_tool_name",
    "normalize_input_schema",
]
