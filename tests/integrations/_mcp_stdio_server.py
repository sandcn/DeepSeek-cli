"""测试用 MCP stdio 服务器（极简 JSON-RPC over stdio）。

仅用于 tests/ 下的 MCP 接入测试：实现 initialize / tools/list / tools/call
三个方法，覆盖文本、结构化、图片、错误四类结果；可选 argv ``--paginate``
让 tools/list 每次只返回一个工具（测试 cursor 分页）。
"""

import base64
import json
import os
import sys

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

TOOLS = [
    {
        "name": "echo",
        "title": "Echo",
        "description": "回显输入文本",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "两数相加",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    },
    {
        "name": "screenshot",
        "description": "返回一张图片",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "boom",
        "description": "始终返回执行错误",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "structured",
        "description": "返回结构化内容",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slow",
        "description": "延迟返回（测试超时）",
        "inputSchema": {"type": "object", "properties": {"seconds": {"type": "number"}}},
    },
    {
        "name": "env",
        "description": "读取本子进程的环境变量",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
]


def _write(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _call_tool(name, args):
    try:
        if name == "echo":
            return {"content": [{"type": "text", "text": str(args.get("text", ""))}]}
        if name == "add":
            total = float(args.get("a", 0)) + float(args.get("b", 0))
            return {"content": [{"type": "text", "text": str(total)}]}
        if name == "screenshot":
            return {"content": [
                {"type": "text", "text": "这是一张 1x1 图片"},
                {"type": "image", "data": base64.b64encode(_PNG_1PX).decode("ascii"), "mimeType": "image/png"},
            ]}
        if name == "boom":
            return {"content": [{"type": "text", "text": "执行失败：模拟错误"}], "isError": True}
        if name == "structured":
            return {
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "structuredContent": {"ok": True},
            }
        if name == "slow":
            import time
            time.sleep(float(args.get("seconds", 1)))
            return {"content": [{"type": "text", "text": "done"}]}
        if name == "env":
            key = str(args.get("key", ""))
            return {"content": [{"type": "text", "text": os.environ.get(key, "<unset>")}]}
    except (TypeError, ValueError):
        return {"content": [{"type": "text", "text": "参数非法"}], "isError": True}
    return None


def main():
    paginate = "--paginate" in sys.argv
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if "id" not in msg:
            continue  # 通知（如 notifications/initialized）
        rid = msg["id"]
        method = msg.get("method")

        if method == "initialize":
            _write({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "test-server", "version": "1.0.0"},
                "instructions": "这是测试 MCP 服务器。",
            }})
        elif method == "tools/list":
            cursor = (msg.get("params") or {}).get("cursor")
            if paginate:
                try:
                    index = int(cursor) if cursor else 0
                except (TypeError, ValueError):
                    index = 0
                index = max(0, min(index, len(TOOLS) - 1))
                result = {"tools": [TOOLS[index]]}
                if index + 1 < len(TOOLS):
                    result["nextCursor"] = str(index + 1)
                _write({"jsonrpc": "2.0", "id": rid, "result": result})
            else:
                _write({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            result = _call_tool(params.get("name"), params.get("arguments") or {})
            if result is None:
                _write({"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32602, "message": f"未知工具: {params.get('name')}"}})
            else:
                _write({"jsonrpc": "2.0", "id": rid, "result": result})
        else:
            _write({"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": f"未实现方法: {method}"}})


if __name__ == "__main__":
    main()
