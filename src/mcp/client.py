"""MCP 客户端 — 生命周期握手 + 工具发现 + 工具调用。

一个 McpClient 对应一个 MCP server（配置条目），内部持有一个传输实例。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import McpServerConfig
from .errors import McpError
from .protocol import METHOD_TOOLS_CALL, METHOD_TOOLS_LIST
from .transport import create_transport

_logger = logging.getLogger(__name__)

#: 工具列表分页的安全上限（防服务端 bug 导致死循环）
_MAX_TOOL_PAGES = 100

#: 单张图片 base64 体积上限（与 api.multimodal 内联图片限制一致）——
#: 超限图片不进 content blocks，只保留文本占位，避免请求体被超大图撑爆。
_MAX_IMAGE_BASE64 = 32 * 1024 * 1024


@dataclass
class McpToolDef:
    """MCP 工具定义（tools/list 条目）。"""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)
    title: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> Optional["McpToolDef"]:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        schema = raw.get("inputSchema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        title = raw.get("title")
        description = raw.get("description")
        return cls(
            name=name.strip(),
            description=description if isinstance(description, str) else "",
            input_schema=schema,
            title=title if isinstance(title, str) else "",
        )


@dataclass
class McpToolResult:
    """MCP 工具调用结果（已归一化）。"""

    text: str = ""
    images: list = field(default_factory=list)
    is_error: bool = False
    structured: Any = None

    def content_blocks(self) -> list:
        """图片 content blocks（OpenAI 兼容 image_url data URI）。"""
        blocks: list = []
        for img in self.images:
            mime = img.get("mimeType") or "image/png"
            data = img.get("data") or ""
            if not data:
                continue
            blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
        return blocks

    @classmethod
    def from_result(cls, result: Any) -> "McpToolResult":
        """将 tools/call 的 result 归一化为文本 + 图片 + 结构化内容。"""
        if not isinstance(result, dict):
            return cls(text=str(result) if result is not None else "")

        is_error = bool(result.get("isError"))
        texts: list = []
        images: list = []
        content = result.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    texts.append(str(block))
                    continue
                btype = block.get("type")
                if btype == "text":
                    texts.append(str(block.get("text") or ""))
                elif btype == "image":
                    data = block.get("data") or ""
                    mime = block.get("mimeType") or "image/png"
                    if data and len(data) <= _MAX_IMAGE_BASE64:
                        images.append({"mimeType": mime, "data": data})
                        texts.append(f"[图片: {mime}]")
                    elif data:
                        _logger.warning(
                            "MCP 图片过大（base64 %.1f MiB > 上限 %.0f MiB），"
                            "已跳过图片内容", len(data) / 1048576,
                            _MAX_IMAGE_BASE64 / 1048576,
                        )
                        texts.append(f"[图片过大已省略: {mime}, {len(data)} 字节 base64]")
                    else:
                        texts.append(f"[图片: {mime}]")
                elif btype == "audio":
                    texts.append(f"[音频: {block.get('mimeType') or 'audio'}]")
                elif btype == "resource_link":
                    label = block.get("name") or block.get("uri") or ""
                    texts.append(f"[资源链接: {label}]")
                elif btype == "resource":
                    res = block.get("resource")
                    if isinstance(res, dict) and isinstance(res.get("text"), str):
                        texts.append(res["text"])
                    else:
                        uri = res.get("uri") if isinstance(res, dict) else ""
                        texts.append(f"[资源: {uri or ''}]")
                else:
                    try:
                        texts.append(json.dumps(block, ensure_ascii=False)[:500])
                    except (TypeError, ValueError):
                        texts.append(str(block)[:500])

        structured = result.get("structuredContent")
        if structured is not None and not texts:
            try:
                texts.append(json.dumps(structured, ensure_ascii=False))
            except (TypeError, ValueError):
                texts.append(str(structured))

        text = "\n".join(t for t in texts if t)
        if not text and images:
            text = f"[{len(images)} 张图片]"
        return cls(text=text, images=images, is_error=is_error, structured=structured)


class McpClient:
    """单个 MCP server 的客户端。"""

    def __init__(self, cfg: McpServerConfig):
        self.config = cfg
        self._transport = create_transport(cfg)
        self._connected = False
        self.tools: list = []

    # ── 属性 ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def server_info(self) -> dict:
        return self._transport.server_info

    @property
    def instructions(self) -> str:
        return self._transport.instructions

    @property
    def protocol_version(self) -> str:
        return self._transport.protocol_version

    # ── 生命周期 ──────────────────────────────────────────

    async def connect(self) -> None:
        """建立传输并完成 initialize 握手。"""
        if self._connected:
            return
        await self._transport.start()
        try:
            await self._transport.initialize()
        except Exception:
            await self.close()
            raise
        self._connected = True
        _logger.info(
            "MCP server '%s' 已连接（transport=%s, protocol=%s, server=%s）",
            self.name, self.config.transport, self.protocol_version,
            self.server_info.get("name", "?"),
        )

    async def list_tools(self) -> list:
        """拉取工具列表（支持 cursor 分页）。"""
        tools: list = []
        cursor: Optional[str] = None
        pages = 0
        for page in range(_MAX_TOOL_PAGES):
            pages = page + 1
            params = {"cursor": cursor} if cursor else {}
            result = await self._transport.request(METHOD_TOOLS_LIST, params)
            if not isinstance(result, dict):
                raise McpError(f"MCP server '{self.name}' 的 tools/list 响应非法")
            for raw in (result.get("tools") or []):
                tool_def = McpToolDef.from_raw(raw)
                if tool_def is not None:
                    tools.append(tool_def)
            next_cursor = result.get("nextCursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        else:
            _logger.warning(
                "MCP server '%s' 的 tools/list 分页达到上限 %d 页，"
                "结果可能不完整（已获取 %d 个工具）",
                self.name, _MAX_TOOL_PAGES, len(tools),
            )
        self.tools = tools
        return tools

    async def call_tool(self, name: str, arguments: Optional[dict] = None) -> McpToolResult:
        """调用工具并归一化结果。"""
        if not self._connected:
            raise McpError(f"MCP server '{self.name}' 未连接")
        result = await self._transport.request(
            METHOD_TOOLS_CALL,
            {"name": name, "arguments": arguments or {}},
        )
        return McpToolResult.from_result(result)

    async def close(self) -> None:
        self._connected = False
        try:
            await self._transport.close()
        except Exception:
            _logger.debug("关闭 MCP server '%s' 异常", self.name, exc_info=True)


__all__ = ["McpToolDef", "McpToolResult", "McpClient"]
