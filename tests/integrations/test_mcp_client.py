"""MCP 客户端（McpClient）单元测试 — 用假传输隔离 IO。"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp import client as client_mod
from src.mcp.client import McpClient, McpToolDef, McpToolResult
from src.mcp.config import McpServerConfig
from src.mcp.errors import McpError


class FakeTransport:
    """记录调用并返回预设结果的假传输。"""

    def __init__(self, list_pages=None, call_results=None, fail_initialize=False):
        self.started = False
        self.initialized = False
        self.closed = False
        self.notifications = []
        self.requests = []
        self._list_pages = list(list_pages or [])
        self._call_results = dict(call_results or {})
        self._fail_initialize = fail_initialize
        self.protocol_version = "2025-06-18"
        self.server_info = {"name": "fake", "version": "1"}
        self.instructions = "假服务器说明"

    async def start(self):
        self.started = True

    async def initialize(self):
        if self._fail_initialize:
            raise RuntimeError("handshake failed")
        self.initialized = True
        return {}

    async def request(self, method, params=None, timeout=None):
        self.requests.append((method, params))
        if method == "tools/list":
            if self._list_pages:
                return self._list_pages.pop(0)
            return {"tools": []}
        if method == "tools/call":
            key = (params or {}).get("name")
            return self._call_results.get(key, {"content": []})
        return {}

    async def notify(self, method, params=None):
        self.notifications.append((method, params))

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_singletons():
    yield
    from src.mcp.manager import McpManager
    McpManager.reset_default()


def _client(monkeypatch, transport, **cfg_kwargs):
    monkeypatch.setattr(client_mod, "create_transport", lambda cfg: transport)
    cfg = McpServerConfig(name="fake", transport="stdio", command="x", **cfg_kwargs)
    return McpClient(cfg)


def test_connect_handshake(monkeypatch):
    transport = FakeTransport()
    client = _client(monkeypatch, transport)
    asyncio.run(client.connect())
    assert transport.started and transport.initialized
    assert client.connected is True
    assert client.server_info["name"] == "fake"
    assert client.instructions == "假服务器说明"
    assert client.protocol_version == "2025-06-18"


def test_connect_failure_closes_transport(monkeypatch):
    transport = FakeTransport(fail_initialize=True)
    client = _client(monkeypatch, transport)
    with pytest.raises(RuntimeError):
        asyncio.run(client.connect())
    assert transport.closed is True
    assert client.connected is False


def test_list_tools_follows_cursor(monkeypatch):
    transport = FakeTransport(list_pages=[
        {"tools": [{"name": "a", "description": "A", "inputSchema": {"type": "object"}}],
         "nextCursor": "c1"},
        {"tools": [{"name": "b"}], "nextCursor": None},
    ])
    client = _client(monkeypatch, transport)
    client._connected = True
    tools = asyncio.run(client.list_tools())
    assert [t.name for t in tools] == ["a", "b"]
    assert isinstance(tools[0], McpToolDef)
    assert tools[0].description == "A"
    # 第二页带 cursor
    assert transport.requests[-1] == ("tools/list", {"cursor": "c1"})


def test_list_tools_skips_invalid_entries(monkeypatch):
    transport = FakeTransport(list_pages=[{"tools": [None, {}, {"name": ""}, {"name": "ok"}]}])
    client = _client(monkeypatch, transport)
    client._connected = True
    tools = asyncio.run(client.list_tools())
    assert [t.name for t in tools] == ["ok"]


def test_list_tools_rejects_non_object_result(monkeypatch):
    transport = FakeTransport(list_pages=[["nope"]])
    client = _client(monkeypatch, transport)
    client._connected = True
    with pytest.raises(McpError):
        asyncio.run(client.list_tools())


def test_call_tool_requires_connection(monkeypatch):
    transport = FakeTransport()
    client = _client(monkeypatch, transport)
    with pytest.raises(McpError):
        asyncio.run(client.call_tool("echo", {}))


def test_call_tool_returns_normalized_result(monkeypatch):
    transport = FakeTransport(call_results={
        "echo": {"content": [{"type": "text", "text": "hi"}]},
        "screenshot": {"content": [{"type": "image", "data": "QQ==", "mimeType": "image/png"}]},
    })
    client = _client(monkeypatch, transport)
    client._connected = True

    r1 = asyncio.run(client.call_tool("echo", {"text": "hi"}))
    assert isinstance(r1, McpToolResult)
    assert r1.text == "hi"

    r2 = asyncio.run(client.call_tool("screenshot", {}))
    assert r2.images and r2.content_blocks()[0]["image_url"]["url"].startswith("data:image/png")

    assert transport.requests[-1] == ("tools/call", {"name": "screenshot", "arguments": {}})


def test_tool_result_oversized_image_is_dropped(caplog):
    import logging

    huge = "A" * (client_mod._MAX_IMAGE_BASE64 + 1)
    with caplog.at_level(logging.WARNING, logger="src.mcp.client"):
        r = McpToolResult.from_result({"content": [
            {"type": "image", "data": huge, "mimeType": "image/png"},
        ]})
    assert r.images == []
    assert r.content_blocks() == []
    assert "图片过大已省略" in r.text
    assert "过大" in caplog.text


def test_tool_result_image_without_data_is_placeholder_only():
    r = McpToolResult.from_result({"content": [{"type": "image", "mimeType": "image/png"}]})
    assert r.images == []
    assert "[图片: image/png]" in r.text


def test_close_marks_disconnected(monkeypatch):
    transport = FakeTransport()
    client = _client(monkeypatch, transport)
    asyncio.run(client.connect())
    asyncio.run(client.close())
    assert transport.closed is True
    assert client.connected is False
