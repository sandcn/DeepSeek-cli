"""MCP 协议常量与 JSON-RPC 报文工具（无 IO 的纯函数层）。

协议依据：Model Context Protocol 2025-06-18 规范
（JSON-RPC 2.0；stdio 以换行分隔；Streamable HTTP 以 POST + SSE 可选流式）。
"""

from __future__ import annotations

import json
from typing import Any, Optional

# ── 协议常量 ──────────────────────────────────────────────

PROTOCOL_VERSION = "2025-06-18"

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)

CLIENT_NAME = "deepseek-cli"
CLIENT_VERSION = "2.2.0"

JSONRPC_VERSION = "2.0"

METHOD_INITIALIZE = "initialize"
NOTIFICATION_INITIALIZED = "notifications/initialized"
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"
NOTIFICATION_TOOLS_LIST_CHANGED = "notifications/tools/list_changed"

#: Streamable HTTP 响应头（会话 ID / 协议版本）
HEADER_SESSION_ID = "Mcp-Session-Id"
HEADER_PROTOCOL_VERSION = "MCP-Protocol-Version"

#: JSON-RPC 标准错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ── 报文构造 ──────────────────────────────────────────────

def make_request(request_id: Any, method: str, params: Optional[dict] = None) -> dict:
    """构造 JSON-RPC 请求报文。"""
    msg: dict = {"jsonrpc": JSONRPC_VERSION, "id": request_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_notification(method: str, params: Optional[dict] = None) -> dict:
    """构造 JSON-RPC 通知报文（无 id，无响应）。"""
    msg: dict = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def make_error_response(request_id: Any, code: int, message: str) -> dict:
    """构造 JSON-RPC 错误响应报文。"""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def build_initialize_params() -> dict:
    """构造 initialize 请求参数。"""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
    }


# ── 报文解析 ──────────────────────────────────────────────

def parse_message(raw: Any) -> Optional[dict]:
    """解析单条 JSON-RPC 报文（bytes / str / dict），非法返回 None。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def serialize_message(msg: dict) -> str:
    """序列化为单行 JSON（stdio 传输要求无内嵌换行）。"""
    return json.dumps(msg, ensure_ascii=False, separators=(",", ":"))


def is_response(msg: dict) -> bool:
    """是否为 JSON-RPC 响应（含 error 响应）。"""
    return "id" in msg and ("result" in msg or "error" in msg)


def is_request(msg: dict) -> bool:
    """是否为 JSON-RPC 请求（有 id + method）。"""
    return "id" in msg and "method" in msg


def is_notification(msg: dict) -> bool:
    """是否为 JSON-RPC 通知（无 id + method）。"""
    return "id" not in msg and "method" in msg


def negotiate_protocol_version(server_version: Any) -> str:
    """协商协议版本：服务端返回受支持版本时采用，否则沿用客户端首选版本。"""
    if isinstance(server_version, str) and server_version in SUPPORTED_PROTOCOL_VERSIONS:
        return server_version
    return PROTOCOL_VERSION


__all__ = [
    "PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "CLIENT_NAME",
    "CLIENT_VERSION",
    "JSONRPC_VERSION",
    "METHOD_INITIALIZE",
    "NOTIFICATION_INITIALIZED",
    "METHOD_TOOLS_LIST",
    "METHOD_TOOLS_CALL",
    "NOTIFICATION_TOOLS_LIST_CHANGED",
    "HEADER_SESSION_ID",
    "HEADER_PROTOCOL_VERSION",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "make_request",
    "make_notification",
    "make_error_response",
    "build_initialize_params",
    "parse_message",
    "serialize_message",
    "is_response",
    "is_request",
    "is_notification",
    "negotiate_protocol_version",
]
