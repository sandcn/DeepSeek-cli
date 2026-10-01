"""MCP 工具适配层（动态 Func 子类 / 工具名 / schema）单元测试。"""

from __future__ import annotations

import asyncio
import json

from src.mcp.client import McpToolDef, McpToolResult
from src.mcp.tool import (
    MCP_TOOL_PREFIX,
    build_mcp_tool_class,
    is_mcp_tool,
    mcp_tool_name,
    normalize_input_schema,
    sanitize_identifier,
)
from src.tools.base import Func, ToolResult, get_tool_metadata


class _FakeAgent:
    def __init__(self, model):
        self.model = model


class _FakeManager:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def call_tool(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        return self._result


def _make_tool_def(name="echo", schema=None, description="回显"):
    return McpToolDef(
        name=name,
        description=description,
        input_schema=schema if schema is not None else {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )


# ── 工具名 ─────────────────────────────────────────────

def test_mcp_tool_name_basic():
    assert mcp_tool_name("filesystem", "read_file") == "mcp__filesystem__read_file"


def test_mcp_tool_name_sanitizes_illegal_chars():
    name = mcp_tool_name("my server!", "read.file/v2")
    assert name == "mcp__my_server___read_file_v2"


def test_sanitize_identifier():
    assert sanitize_identifier("a b-c_d.e") == "a_b-c_d_e"
    assert sanitize_identifier(None) == ""


def test_mcp_tool_name_empty_parts_get_placeholder():
    assert mcp_tool_name("", "") == "mcp__server__tool"


def test_mcp_tool_name_truncated_to_64_and_unique():
    a = mcp_tool_name("a" * 60 + "x", "tool" * 20 + "x")
    b = mcp_tool_name("a" * 60 + "y", "tool" * 20 + "y")
    assert len(a) <= 64 and len(b) <= 64
    assert a.startswith(MCP_TOOL_PREFIX)
    assert a != b


def test_is_mcp_tool():
    assert is_mcp_tool("mcp__a__b") is True
    assert is_mcp_tool("read_file") is False
    assert is_mcp_tool(None) is False
    assert is_mcp_tool(123) is False


# ── schema 归一化 ──────────────────────────────────────

def test_normalize_input_schema_fills_object():
    assert normalize_input_schema(None) == {"type": "object", "properties": {}}
    assert normalize_input_schema({"properties": {"a": {}}}) == {
        "type": "object", "properties": {"a": {}},
    }
    assert normalize_input_schema({"type": "object"})["properties"] == {}


def test_normalize_input_schema_drops_bad_required():
    out = normalize_input_schema({"type": "object", "required": "a"})
    assert "required" not in out


# ── 动态工具类 ─────────────────────────────────────────

def test_build_class_registers_schema_and_metadata():
    cls = build_mcp_tool_class("fs", _make_tool_def())
    assert issubclass(cls, Func)
    assert cls.name == "mcp__fs__echo"
    schema = cls.to_tool_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "mcp__fs__echo"
    assert schema["function"]["parameters"]["properties"]["text"]["type"] == "string"
    meta = get_tool_metadata(cls)
    assert meta is not None
    assert meta.category == "mcp"
    assert meta.requires_network is True
    assert meta.tool_category == "general"


def test_build_class_description_includes_server():
    cls = build_mcp_tool_class("fs", _make_tool_def(description="回显文本"))
    assert "[MCP:fs]" in cls.to_tool_schema()["function"]["description"]
    assert "回显文本" in cls.to_tool_schema()["function"]["description"]


def test_from_args_passes_arguments_through():
    cls = build_mcp_tool_class("fs", _make_tool_def())
    tool = cls.from_args({"text": "hi", "extra": 1})
    assert tool.arguments == {"text": "hi", "extra": 1}
    # JSON 字符串形态
    tool2 = cls.from_args('{"text": "yo"}')
    assert tool2.arguments == {"text": "yo"}
    # 非法/非 dict → 空参数
    assert cls.from_args("not json").arguments == {}
    assert cls.from_args([1, 2]).arguments == {}
    assert cls.from_args(None).arguments == {}


def test_display_params():
    cls = build_mcp_tool_class("fs", _make_tool_def())
    assert cls.display_params({}) == ""
    assert cls.display_params({"text": "hello"}) == "text=hello"
    long_args = {"text": "x" * 200}
    assert len(cls.display_params(long_args)) <= 80
    assert cls.display_params(long_args).endswith("...")


def test_execute_delegates_to_manager(monkeypatch):
    from src.mcp import manager as manager_mod

    fake = _FakeManager(McpToolResult(text="hi"))
    monkeypatch.setattr(manager_mod.McpManager, "default", classmethod(lambda cls: fake))

    cls = build_mcp_tool_class("fs", _make_tool_def())
    tool = cls.from_args({"text": "hi"})
    result = asyncio.run(tool.execute())
    assert result == "hi"
    assert fake.calls == [("fs", "echo", {"text": "hi"})]


def test_execute_returns_placeholder_when_empty(monkeypatch):
    from src.mcp import manager as manager_mod

    fake = _FakeManager(McpToolResult(text=""))
    monkeypatch.setattr(manager_mod.McpManager, "default", classmethod(lambda cls: fake))

    cls = build_mcp_tool_class("fs", _make_tool_def())
    tool = cls.from_args({})
    assert "无输出" in asyncio.run(tool.execute())


def test_execute_attaches_image_blocks_for_multimodal_model(monkeypatch):
    from src.mcp import manager as manager_mod

    result = McpToolResult(text="[图片: image/png]", images=[{"mimeType": "image/png", "data": "QUJD"}])
    fake = _FakeManager(result)
    monkeypatch.setattr(manager_mod.McpManager, "default", classmethod(lambda cls: fake))

    cls = build_mcp_tool_class("fs", _make_tool_def(name="screenshot"))
    tool = cls.from_args({})
    tool.set_agent(_FakeAgent("deepseek-flash"))
    text = asyncio.run(tool.execute())
    assert text == "[图片: image/png]"
    assert tool.result_blocks is not None
    assert tool.result_blocks[0]["type"] == "text"
    assert tool.result_blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_execute_skips_image_blocks_for_text_model(monkeypatch):
    from src.mcp import manager as manager_mod

    result = McpToolResult(text="[图片: image/png]", images=[{"mimeType": "image/png", "data": "QUJD"}])
    fake = _FakeManager(result)
    monkeypatch.setattr(manager_mod.McpManager, "default", classmethod(lambda cls: fake))

    cls = build_mcp_tool_class("fs", _make_tool_def(name="screenshot"))
    tool = cls.from_args({})
    tool.set_agent(_FakeAgent("deepseek-v4-pro"))
    asyncio.run(tool.execute())
    assert tool.result_blocks is None


def test_execute_wraps_error_result():
    from src.mcp import manager as manager_mod

    fake = _FakeManager(McpToolResult(text="服务端报错", is_error=True))
    orig = manager_mod.McpManager.default
    try:
        manager_mod.McpManager.default = classmethod(lambda cls: fake)
        cls = build_mcp_tool_class("fs", _make_tool_def())
        out = asyncio.run(cls.from_args({}).execute())
        assert out == "(MCP 工具执行失败: 服务端报错)"
    finally:
        manager_mod.McpManager.default = orig


def test_execute_keeps_paren_prefixed_error_result():
    from src.mcp import manager as manager_mod

    fake = _FakeManager(McpToolResult(text="(已失败)", is_error=True))
    orig = manager_mod.McpManager.default
    try:
        manager_mod.McpManager.default = classmethod(lambda cls: fake)
        cls = build_mcp_tool_class("fs", _make_tool_def())
        assert asyncio.run(cls.from_args({}).execute()) == "(已失败)"
    finally:
        manager_mod.McpManager.default = orig


def test_mcp_tool_name_disambiguate_forces_hash():
    plain = mcp_tool_name("srv", "echo")
    hashed = mcp_tool_name("srv", "echo", disambiguate=True)
    assert plain != hashed
    assert hashed.startswith(MCP_TOOL_PREFIX)
    assert len(hashed) <= 64


def test_build_class_disambiguate_param():
    cls = build_mcp_tool_class("srv", _make_tool_def(), disambiguate=True)
    assert cls.name != "mcp__srv__echo"
    assert cls.name.startswith("mcp__srv__echo")


def test_content_blocks_exclude_empty_image_data():
    result = McpToolResult(images=[{"mimeType": "image/png", "data": ""}])
    assert result.content_blocks() == []


# ── McpToolResult 归一化 ───────────────────────────────

def test_tool_result_from_text_content():
    r = McpToolResult.from_result({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]})
    assert r.text == "a\nb"
    assert r.is_error is False


def test_tool_result_from_error_content():
    r = McpToolResult.from_result({"content": [{"type": "text", "text": "bad"}], "isError": True})
    assert r.is_error is True
    assert r.text == "bad"


def test_tool_result_from_image_content():
    r = McpToolResult.from_result({"content": [{"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}]})
    assert r.images == [{"mimeType": "image/jpeg", "data": "QUJD"}]
    assert "图片" in r.text
    assert json.loads(json.dumps(r.content_blocks()))[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_tool_result_from_mixed_and_unknown_blocks():
    r = McpToolResult.from_result({"content": [
        {"type": "audio", "mimeType": "audio/wav"},
        {"type": "resource_link", "name": "main.rs", "uri": "file:///main.rs"},
        {"type": "resource", "resource": {"uri": "file:///x", "text": "内容"}},
        {"type": "unknown-kind", "v": 1},
    ]})
    assert "[音频" in r.text
    assert "main.rs" in r.text
    assert "内容" in r.text


def test_tool_result_from_structured_content_only():
    r = McpToolResult.from_result({"content": [], "structuredContent": {"ok": True}})
    assert json.loads(r.text) == {"ok": True}


def test_tool_result_from_image_only_text_fallback():
    r = McpToolResult.from_result({"content": [{"type": "image", "data": "QQ==", "mimeType": "image/png"}]})
    assert r.images


def test_tool_result_from_non_dict():
    assert McpToolResult.from_result("oops").text == "oops"
    assert McpToolResult.from_result(None).text == ""


def test_tool_result_to_content_via_tool_result_wrapper():
    wrapped = ToolResult(text="x", blocks=[{"type": "text", "text": "x"}])
    assert wrapped.to_content() == [{"type": "text", "text": "x"}]
