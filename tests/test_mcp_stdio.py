"""MCP stdio 传输端到端测试 — 启动真实子进程（tests/_mcp_stdio_server.py）。"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from src.mcp.client import McpClient
from src.mcp.config import McpServerConfig
from src.mcp.errors import McpError, McpProtocolError, McpTransportError
from src.mcp.transport import StdioTransport

_SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_mcp_stdio_server.py")


def _cfg(args=None, timeout=20.0):
    return McpServerConfig(
        name="test",
        transport="stdio",
        command=sys.executable,
        args=[_SERVER] + list(args or []),
        timeout=timeout,
    )


def test_stdio_connect_and_list_tools():
    async def _run():
        client = McpClient(_cfg())
        try:
            await client.connect()
            assert client.connected
            assert client.server_info.get("name") == "test-server"
            assert "测试 MCP 服务器" in client.instructions
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert {"echo", "add", "screenshot", "boom", "structured", "slow"} <= names
            echo = next(t for t in tools if t.name == "echo")
            assert echo.input_schema["properties"]["text"]["type"] == "string"
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_call_tool_text_and_error():
    async def _run():
        client = McpClient(_cfg())
        try:
            await client.connect()
            await client.list_tools()

            ok = await client.call_tool("echo", {"text": "你好"})
            assert ok.text == "你好" and ok.is_error is False

            added = await client.call_tool("add", {"a": 2, "b": 3})
            assert float(added.text) == 5.0

            bad = await client.call_tool("boom", {})
            assert bad.is_error is True
            assert "模拟错误" in bad.text

            structured = await client.call_tool("structured", {})
            assert structured.structured == {"ok": True}
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_image_result():
    async def _run():
        client = McpClient(_cfg())
        try:
            await client.connect()
            await client.list_tools()
            result = await client.call_tool("screenshot", {})
            assert result.images and result.images[0]["mimeType"] == "image/png"
            assert result.content_blocks()[0]["image_url"]["url"].startswith("data:image/png;base64,")
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_unknown_tool_is_protocol_error():
    async def _run():
        client = McpClient(_cfg())
        try:
            await client.connect()
            await client.list_tools()
            with pytest.raises(McpProtocolError):
                await client.call_tool("nope", {})
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_pagination():
    async def _run():
        client = McpClient(_cfg(["--paginate"]))
        try:
            await client.connect()
            tools = await client.list_tools()
            assert len(tools) >= 5
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_timeout():
    async def _run():
        client = McpClient(_cfg(timeout=0.5))
        try:
            await client.connect()
            with pytest.raises(McpTransportError):
                await client.call_tool("slow", {"seconds": 3})
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_start_failure_for_missing_command():
    cfg = McpServerConfig(name="bad", transport="stdio", command="/nonexistent/binary-xyz")
    transport = StdioTransport(cfg)
    with pytest.raises(McpTransportError):
        asyncio.run(transport.start())


def test_stdio_request_after_close_raises():
    async def _run():
        client = McpClient(_cfg())
        await client.connect()
        await client.close()
        with pytest.raises(McpError):
            await client.call_tool("echo", {"text": "x"})

    asyncio.run(_run())


# ── 子进程环境变量白名单 ────────────────────────────────

def test_stdio_child_env_whitelist_blocks_credentials(monkeypatch):
    monkeypatch.setenv("MCP_TEST_SECRET", "s3cr3t")

    async def _run():
        client = McpClient(_cfg())
        try:
            await client.connect()
            await client.list_tools()
            leaked = await client.call_tool("env", {"key": "MCP_TEST_SECRET"})
            assert leaked.text == "<unset>"
            path = await client.call_tool("env", {"key": "PATH"})
            assert path.text and path.text != "<unset>"
        finally:
            await client.close()

    asyncio.run(_run())


def test_stdio_child_env_explicit_and_inherit(monkeypatch):
    monkeypatch.setenv("MCP_TEST_SECRET", "s3cr3t")

    async def _run():
        explicit_cfg = _cfg()
        explicit_cfg.env = {"MCP_EXPLICIT": "value"}
        client = McpClient(explicit_cfg)
        try:
            await client.connect()
            got = await client.call_tool("env", {"key": "MCP_EXPLICIT"})
            assert got.text == "value"
            # 未显式声明且非白名单 → 仍不可见
            hidden = await client.call_tool("env", {"key": "MCP_TEST_SECRET"})
            assert hidden.text == "<unset>"
        finally:
            await client.close()

        inherit_cfg = _cfg()
        inherit_cfg.inherit_env = True
        client2 = McpClient(inherit_cfg)
        try:
            await client2.connect()
            got2 = await client2.call_tool("env", {"key": "MCP_TEST_SECRET"})
            assert got2.text == "s3cr3t"
        finally:
            await client2.close()

    asyncio.run(_run())


def test_stdio_start_is_idempotent():
    async def _run():
        transport = StdioTransport(_cfg())
        await transport.start()
        proc = transport._proc
        await transport.start()  # 第二次不应重复拉起子进程
        assert transport._proc is proc
        await transport.close()

    asyncio.run(_run())
