"""web_search / web_fetch 重构后契约测试（DeepSeek 官方原生搜索）。

对齐 DSH dsh-tool-web + dsh-web-search-deepseek 的契约与内部实现：
1. web_search 入参仅 query（无 mode/engine/time_range/num_results）
2. 执行走 DeepSeekSearchProvider：Anthropic 兼容 Messages API + 原生服务端
   工具 web_search_20250305；工具本身不解析搜索引擎 HTML
3. 响应映射：web_search_tool_result 块 → 来源（title/link）；摘要片段来自
   text 块 citations（url → cited_text，首见生效）；page_age → published_at；
   按 URL 精确去重（首见生效）
4. 无 web_search_tool_result 块 → WebSearchError(WEB_PROVIDER_ERROR)；
   未配置密钥 → WebSearchError(WEB_PROVIDER_CREDENTIAL_MISSING)
5. 输出：来源列表（标题 markdown 链接 + 摘要片段 + 发布日期）+ 截断提示 +
   引用指令；无来源 → "未找到结果。"；数量上限 8（对齐 DSH WEB_SEARCH_MAX_RESULTS）
6. web_fetch 独立为单 url 参数的获取工具（不变）
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from src.tools.search_providers import (
    DeepSeekSearchProvider,
    SearchResult,
    WebSearchError,
    _citation_snippets,
    _resolve_api_key,
)
from src.tools.web_fetch import WebFetchFunc
from src.tools.web_search import WebSearchFunc


# ── schema 契约 ──

def test_schema_single_query_param():
    schema = WebSearchFunc.to_tool_schema()
    fn = schema["function"]
    assert fn["name"] == "web_search"
    props = fn["parameters"]["properties"]
    assert list(props) == ["query"]
    assert fn["parameters"]["required"] == ["query"]
    # 旧接口参数已移除
    assert "mode" not in props
    assert "engine" not in props
    assert "time_range" not in props
    assert "num_results" not in props


def test_web_fetch_schema_single_url_param():
    schema = WebFetchFunc.to_tool_schema()
    fn = schema["function"]
    assert fn["name"] == "web_fetch"
    props = fn["parameters"]["properties"]
    assert list(props) == ["url"]
    assert fn["parameters"]["required"] == ["url"]


def test_from_args_only_query():
    tool = WebSearchFunc.from_args({"query": "hello"})
    assert tool.query == "hello"


# ── 提供者响应映射（对齐 DSH mapAnthropicResponse） ──

_SAMPLE_RESPONSE = {
    "content": [
        {
            "type": "text",
            "text": "pytest 是 Python 的测试框架。",
            "citations": [
                {"url": "https://docs.pytest.org/", "cited_text": "pytest 官方文档摘要"},
                {"url": "https://pypi.org/project/pytest/", "cited_text": "PyPI 页面摘要"},
                {"url": "", "cited_text": "空 URL 应被忽略"},
                {"url": "https://no-text.example.com/", "cited_text": ""},
            ],
        },
        {
            "type": "web_search_tool_result",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://docs.pytest.org/",
                    "title": "pytest 文档",
                    "page_age": "2025-06-01",
                },
                {
                    "type": "web_search_result",
                    "url": "https://pypi.org/project/pytest/",
                    "title": "pytest · PyPI",
                },
                {
                    "type": "web_search_result",
                    "url": "https://docs.pytest.org/",
                    "title": "重复条目（应被去重）",
                },
                {"type": "not_a_search_result", "url": "https://ignored.example.com/"},
            ],
        },
    ],
}


def test_parse_maps_result_blocks_with_citation_snippets():
    result = DeepSeekSearchProvider._parse(_SAMPLE_RESPONSE)
    assert result.answer is None  # DeepSeek 提供者不生成回答（对齐 DSH）
    assert len(result.sources) == 2

    first = result.sources[0]
    assert first["title"] == "pytest 文档"
    assert first["link"] == "https://docs.pytest.org/"
    assert first["snippet"] == "pytest 官方文档摘要"
    assert first["published_at"] == "2025-06-01"

    second = result.sources[1]
    assert second["title"] == "pytest · PyPI"
    assert second["snippet"] == "PyPI 页面摘要"
    assert "published_at" not in second  # 无 page_age 时省略字段


def test_parse_dedupes_by_url_first_wins():
    result = DeepSeekSearchProvider._parse(_SAMPLE_RESPONSE)
    titles = [s["title"] for s in result.sources]
    assert titles == ["pytest 文档", "pytest · PyPI"]
    assert not any("重复" in t for t in titles)


def test_parse_empty_items_returns_no_sources():
    data = {"content": [{"type": "web_search_tool_result", "content": []}]}
    result = DeepSeekSearchProvider._parse(data)
    assert result.answer is None
    assert result.sources == []


def test_parse_without_result_blocks_raises_provider_error():
    with pytest.raises(WebSearchError) as exc_info:
        DeepSeekSearchProvider._parse(
            {"content": [{"type": "text", "text": "只有文本块", "citations": []}]}
        )
    assert exc_info.value.code == "WEB_PROVIDER_ERROR"


def test_parse_missing_content_field_raises_provider_error():
    with pytest.raises(WebSearchError) as exc_info:
        DeepSeekSearchProvider._parse({})
    assert exc_info.value.code == "WEB_PROVIDER_ERROR"


# ── citation 摘要提取（对齐 DSH citationSnippets） ──

def test_citation_snippets_first_occurrence_wins():
    blocks = [
        {"type": "text", "citations": [
            {"url": "https://a.example.com/", "cited_text": "第一段"},
        ]},
        {"type": "not_text", "citations": [
            {"url": "https://ignored.example.com/", "cited_text": "非 text 块应跳过"},
        ]},
        {"type": "text", "citations": [
            {"url": "https://a.example.com/", "cited_text": "第二段（应被忽略）"},
            {"url": "https://b.example.com/", "cited_text": "B 摘要"},
        ]},
        {"type": "text", "citations": None},
    ]
    snippets = _citation_snippets(blocks)
    assert snippets == {
        "https://a.example.com/": "第一段",
        "https://b.example.com/": "B 摘要",
    }


# ── 结构化错误 ──

def test_web_search_error_default_code():
    err = WebSearchError("boom")
    assert err.code == "WEB_PROVIDER_ERROR"
    assert str(err) == "boom"


def test_resolve_api_key_missing_raises_credential_error(monkeypatch):
    monkeypatch.delenv("CHAT_API_KEY", raising=False)
    with pytest.raises(WebSearchError) as exc_info:
        _resolve_api_key()
    assert exc_info.value.code == "WEB_PROVIDER_CREDENTIAL_MISSING"


def test_resolve_api_key_returns_env_key(monkeypatch):
    monkeypatch.setenv("CHAT_API_KEY", "dummy-key")
    assert _resolve_api_key() == "dummy-key"


# ── 数量裁剪（对齐 DSH WebRuntime.capSources） ──

def test_cap_sources_within_limit_unchanged():
    sources = [{"title": f"s{i}", "link": f"https://x.com/{i}"} for i in range(5)]
    out, truncated = WebSearchFunc._cap_sources(sources, 8)
    assert out == sources
    assert truncated is False


def test_cap_sources_over_limit_truncates_and_flags():
    sources = [{"title": f"s{i}", "link": f"https://x.com/{i}"} for i in range(12)]
    out, truncated = WebSearchFunc._cap_sources(sources, 8)
    assert len(out) == 8
    assert out == sources[:8]
    assert truncated is True


# ── 输出格式（对齐 DSH formatSearchOutput） ──

def test_format_result_sources_and_cite_instruction():
    sources = [{
        "title": "pytest 文档",
        "link": "https://docs.pytest.org/",
        "snippet": "pytest 官方文档摘要",
        "published_at": "2025-06-01",
    }]
    text = WebSearchFunc._format_result("q", None, sources)
    assert "来源 (1 条):" in text
    assert "- [pytest 文档](https://docs.pytest.org/) — pytest 官方文档摘要 (2025-06-01)" in text
    assert "markdown 链接引用" in text  # 引用指令


def test_format_result_with_answer():
    sources = [{"title": "标题", "link": "https://example.com", "snippet": "摘要"}]
    text = WebSearchFunc._format_result("q", "这是答案", sources)
    assert text.startswith("答案: 这是答案")
    assert "- [标题](https://example.com)" in text


def test_format_result_label_falls_back_to_hostname():
    sources = [{"title": "", "link": "https://example.com/a/b", "snippet": "摘要"}]
    text = WebSearchFunc._format_result("q", None, sources)
    assert "- [example.com](https://example.com/a/b)" in text


def test_format_result_no_sources():
    text = WebSearchFunc._format_result("q", None, [])
    assert "未找到结果。" in text
    assert "markdown 链接引用" in text


def test_format_result_truncated_note():
    sources = [{"title": f"s{i}", "link": f"https://x.com/{i}", "snippet": ""} for i in range(8)]
    text = WebSearchFunc._format_result("q", None, sources, truncated=True)
    assert "仅显示前 8 条来源" in text


def test_format_result_truncates_snippet():
    long = "x" * 300
    text = WebSearchFunc._format_result(
        "q", None, [{"title": "t", "link": "https://e.com", "snippet": long}]
    )
    assert "x" * 201 not in text
    assert text.count("...") >= 1


# ── 工具执行（集成） ──

def test_execute_formats_provider_sources(monkeypatch):
    class FakeProvider:
        async def search(self, query, client=None):
            assert query == "q"
            return SearchResult(
                sources=[
                    {"title": "s1", "link": "https://a.com/1", "snippet": "snip"},
                    {"title": "s2", "link": "https://b.com/2", "snippet": "snip2"},
                ]
            )

    monkeypatch.setattr(WebSearchFunc, "PROVIDER", FakeProvider)
    result = asyncio.run(WebSearchFunc(query="q").execute())
    assert "- [s1](https://a.com/1) — snip" in result
    assert "- [s2](https://b.com/2) — snip2" in result
    assert "markdown 链接引用" in result


def test_execute_caps_sources_at_max_results(monkeypatch):
    class FakeProvider:
        async def search(self, query, client=None):
            return SearchResult(
                sources=[
                    {"title": f"s{i}", "link": f"https://x.com/{i}", "snippet": ""}
                    for i in range(12)
                ]
            )

    monkeypatch.setattr(WebSearchFunc, "PROVIDER", FakeProvider)
    result = asyncio.run(WebSearchFunc(query="q").execute())
    assert "来源 (8 条):" in result
    assert "仅显示前 8 条来源" in result
    assert "https://x.com/11" not in result  # 第 12 条被裁剪


def test_execute_no_sources(monkeypatch):
    class FakeProvider:
        async def search(self, query, client=None):
            return SearchResult()

    monkeypatch.setattr(WebSearchFunc, "PROVIDER", FakeProvider)
    result = asyncio.run(WebSearchFunc(query="q").execute())
    assert "未找到结果。" in result


def test_execute_provider_error_reports_code(monkeypatch):
    class FakeProvider:
        async def search(self, query, client=None):
            raise WebSearchError("API down", "WEB_PROVIDER_ERROR")

    monkeypatch.setattr(WebSearchFunc, "PROVIDER", FakeProvider)
    result = asyncio.run(WebSearchFunc(query="q").execute())
    assert result.startswith("(")
    assert "[WEB_PROVIDER_ERROR]" in result
    assert "API down" in result


def test_execute_timeout_reports_timeout(monkeypatch):
    class FakeProvider:
        async def search(self, query, client=None):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(WebSearchFunc, "PROVIDER", FakeProvider)
    result = asyncio.run(WebSearchFunc(query="q").execute())
    assert result.startswith("(搜索超时:")


def test_execute_empty_query_returns_error():
    tool = WebSearchFunc(query="   ")
    result = asyncio.run(tool.execute())
    assert result.startswith("(")


def test_web_fetch_execute_empty_url_returns_error():
    tool = WebFetchFunc(url="   ")
    result = asyncio.run(tool.execute())
    assert result.startswith("(")


# ── display_params ──

def test_display_params_query_only():
    assert WebSearchFunc.display_params({"query": "hello"}, 80) == "hello"
    assert WebFetchFunc.display_params({"url": "https://example.com"}, 80) == "https://example.com"
