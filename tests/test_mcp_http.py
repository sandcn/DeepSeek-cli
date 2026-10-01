"""MCP HTTP 传输测试。

- HttpTransport（Streamable HTTP）：httpx.MockTransport 注入，覆盖 JSON 响应 /
  SSE 响应 / 会话头回传 / 202 通知 / HTTP 错误 / JSON-RPC 错误。
- SseTransport（旧式 HTTP+SSE）：本地线程化 HTTP 服务器端到端。
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from src.mcp.client import McpClient
from src.mcp.config import McpServerConfig
from src.mcp.errors import McpProtocolError, McpTransportError
from src.mcp.transport import HttpTransport, SseTransport


# ═══════════════════════════════════════════════════════════════
# Streamable HTTP（MockTransport）
# ═══════════════════════════════════════════════════════════════

async def _make_http_transport(handler, **cfg_kwargs):
    cfg = McpServerConfig(name="h", transport="http", url="https://example.test/mcp",
                          timeout=5.0, **cfg_kwargs)
    transport = HttpTransport(cfg)
    await transport.start()
    await transport._client.aclose()
    transport._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return transport


def test_http_json_flow_and_session_header():
    seen_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method == "initialize":
            return httpx.Response(200, headers={
                "content-type": "application/json",
                "Mcp-Session-Id": "sess-1",
            }, json={"jsonrpc": "2.0", "id": body["id"], "result": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "mock"},
                "capabilities": {"tools": {}},
            }})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "tools/list":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"jsonrpc": "2.0", "id": body["id"],
                                        "result": {"tools": [{"name": "t1"}]}})
        if method == "tools/call":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"jsonrpc": "2.0", "id": body["id"],
                                        "result": {"content": [{"type": "text", "text": "ok"}]}})
        return httpx.Response(400)

    async def _run():
        transport = await _make_http_transport(handler)
        client = McpClient.__new__(McpClient)
        client.config = transport._cfg
        client._transport = transport
        client._connected = False
        client.tools = []
        await client.connect()
        tools = await client.list_tools()
        assert [t.name for t in tools] == ["t1"]
        result = await client.call_tool("t1", {})
        assert result.text == "ok"
        await client.close()

    asyncio.run(_run())
    # initialize 之后的所有请求都应带会话 ID 与协议版本头
    post_headers = [h for h in seen_headers if "mcp-session-id" in {k.lower() for k in h}]
    assert post_headers
    assert all(h.get("mcp-protocol-version") or h.get("MCP-Protocol-Version") for h in seen_headers)


def test_http_sse_response_is_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        if method == "notifications/initialized":
            return httpx.Response(202)
        if method == "initialize":
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"],
                              "result": {"tools": [{"name": "sse-tool"}]}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=f"event: message\ndata: {payload}\n\n".encode())

    async def _run():
        transport = await _make_http_transport(handler)
        await transport.initialize()
        result = await transport.request("tools/list", {})
        assert result["tools"][0]["name"] == "sse-tool"
        await transport.close()

    asyncio.run(_run())


def test_http_error_status_raises_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    async def _run():
        transport = await _make_http_transport(handler)
        with pytest.raises(McpTransportError):
            await transport.request("tools/list", {})
        await transport.close()

    asyncio.run(_run())


def test_http_jsonrpc_error_raises_protocol_error():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        return httpx.Response(200, headers={"content-type": "application/json"},
                              json={"jsonrpc": "2.0", "id": body["id"],
                                    "error": {"code": -32602, "message": "bad params"}})

    async def _run():
        transport = await _make_http_transport(handler)
        with pytest.raises(McpProtocolError) as exc:
            await transport.request("tools/call", {"name": "x"})
        assert exc.value.code == -32602
        await transport.close()

    asyncio.run(_run())


def test_http_404_with_session_clears_session():
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(404)

    async def _run():
        transport = await _make_http_transport(handler)
        transport._session_id = "stale"
        with pytest.raises(McpTransportError):
            await transport.request("tools/list", {})
        assert transport._session_id is None
        await transport.close()

    asyncio.run(_run())


def test_http_invalid_json_body_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b"not-json")

    async def _run():
        transport = await _make_http_transport(handler)
        with pytest.raises(McpTransportError):
            await transport.request("tools/list", {})
        await transport.close()

    asyncio.run(_run())


# ═══════════════════════════════════════════════════════════════
# 旧式 HTTP+SSE（本地线程化服务器）
# ═══════════════════════════════════════════════════════════════

class _SseTestServer:
    """最小 HTTP+SSE MCP 服务器（stdlib http.server + 线程）。"""

    def __init__(self):
        self._outbox: "queue.Queue" = queue.Queue()
        self._stopping = False
        self._httpd = None
        self._thread = None
        self.port = 0

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):  # noqa: D401 - 静音
                return

            def do_GET(self):
                if not self.path.startswith("/sse"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"event: endpoint\ndata: /messages\n\n")
                self.wfile.flush()
                while not server._stopping:
                    try:
                        msg = server._outbox.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    payload = json.dumps(msg, ensure_ascii=False)
                    try:
                        self.wfile.write(f"event: message\ndata: {payload}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        break

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw)
                except (ValueError, TypeError):
                    body = {}
                response = server._handle(body)
                if response is not None:
                    server._outbox.put(response)
                self.send_response(202)
                self.end_headers()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def _handle(self, body):
        rid = body.get("id")
        if rid is None:
            return None
        method = body.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "sse-mock"},
                      "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [{"name": "sse-echo", "description": "e",
                                 "inputSchema": {"type": "object", "properties": {}}}]}
        elif method == "tools/call":
            params = body.get("params") or {}
            result = {"content": [{"type": "text", "text": f"called:{params.get('name')}"}]}
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}}
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def stop(self):
        self._stopping = True
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def test_sse_legacy_end_to_end():
    server = _SseTestServer()
    server.start()
    cfg = McpServerConfig(name="sse", transport="sse",
                          url=f"http://127.0.0.1:{server.port}/sse", timeout=10.0)

    async def _run():
        client = McpClient(cfg)
        try:
            await client.connect()
            assert client.server_info.get("name") == "sse-mock"
            tools = await client.list_tools()
            assert [t.name for t in tools] == ["sse-echo"]
            result = await client.call_tool("sse-echo", {})
            assert result.text == "called:sse-echo"
        finally:
            await client.close()

    try:
        asyncio.run(_run())
    finally:
        server.stop()


def test_sse_transport_missing_endpoint_times_out():
    transport = SseTransport.__new__(SseTransport)
    cfg = McpServerConfig(name="sse", transport="sse", url="http://127.0.0.1:9/sse", timeout=1.0)
    SseTransport.__init__(transport, cfg)
    with pytest.raises(McpTransportError):
        asyncio.run(transport.start())
