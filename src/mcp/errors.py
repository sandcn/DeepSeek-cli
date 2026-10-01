"""MCP 异常类型。

分层：
- McpError           — 所有 MCP 相关异常基类
- McpTransportError  — 传输层（子进程退出 / 网络失败 / 超时 / HTTP 状态错误）
- McpProtocolError   — 协议层（JSON-RPC error 响应 / 报文格式非法）
- McpConfigError     — 配置层（server 配置非法）
"""

from __future__ import annotations

from typing import Any, Optional


class McpError(Exception):
    """MCP 相关异常基类。"""


class McpConfigError(McpError):
    """MCP server 配置非法。"""


class McpTransportError(McpError):
    """传输层错误：进程退出、网络失败、请求超时、HTTP 非成功状态。"""


class McpProtocolError(McpError):
    """协议层错误：JSON-RPC error 响应或报文格式非法。"""

    def __init__(self, message: str, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data

    def __str__(self) -> str:
        if self.code is None:
            return super().__str__()
        return f"{super().__str__()} (code={self.code})"


__all__ = [
    "McpError",
    "McpConfigError",
    "McpTransportError",
    "McpProtocolError",
]
