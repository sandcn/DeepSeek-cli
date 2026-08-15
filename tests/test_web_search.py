"""web_search / web_fetch 重构后契约测试。

对齐内置 web_search 的契约与内部实现：
1. web_search 入参仅 query（无 mode/engine/time_range/num_results）
2. 内部通过搜索提供者获取结构化结果（可选回答 + 来源列表），工具不解析 HTML
3. 输出为：可选答案 + 来源列表（标题 markdown 链接 + 来源 URL + 摘要片段）
4. 提供者合并 + URL 去重 + 数量裁剪；单提供者失败不影响整体
5. web_fetch 独立为单 url 参数的获取工具
"""
from __future__ import annotations

import asyncio

from src.tools.web_search import WebSearchFunc
from src.tools.web_fetch import WebFetchFunc
from src.tools.search_providers import (
    DuckDuckGoProvider,
    ScrapeProviders,
    SearchResult,
)


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


# ── DuckDuckGo 提供者解析 ──

_SAMPLE_DDG = {
    "Answer": "pytest 是一个 Python 测试框架",
    "AbstractText": "pytest 使编写小型可读测试变得容易。",
    "AbstractURL": "https://docs.pytest.org/",
    "Heading": "pytest",
    "AbstractSource": "pytest docs",
    "RelatedTopics": [
        {
            "Name": "分组",
            "Topics": [
                {"Text": "pytest 官方文档片段", "FirstURL": "https://docs.pytest.org/en/stable/"},
                {"Text": "", "FirstURL": "https://example.com/empty"},
            ],
        },
        {"Text": "PyPI 上的 pytest", "FirstURL": "https://pypi.org/project/pytest/"},
    ],
}


def test_ddg_parse_answer_and_sources():
    result = DuckDuckGoProvider._parse(_SAMPLE_DDG)
    assert result.answer == "pytest 是一个 Python 测试框架"
    # 主来源 + 2 个 RelatedTopics（空 Text 的被跳过）
    assert len(result.sources) == 3
    assert result.sources[0]["link"] == "https://docs.pytest.org/"
    assert result.sources[1]["link"] == "https://docs.pytest.org/en/stable/"
    assert result.sources[2]["link"] == "https://pypi.org/project/pytest/"
    # 无 Name 时用域名推导标题
    assert result.sources[2]["title"] == "pypi.org"


def test_ddg_parse_abstract_fallback_answer():
    data = {k: v for k, v in _SAMPLE_DDG.items() if k != "Answer"}
    result = DuckDuckGoProvider._parse(data)
    assert result.answer == "pytest 使编写小型可读测试变得容易。"


def test_ddg_parse_empty_payload():
    result = DuckDuckGoProvider._parse({})
    assert result.answer is None
    assert result.sources == []


def test_ddg_flatten_topics():
    flat = DuckDuckGoProvider._flatten_topics(_SAMPLE_DDG["RelatedTopics"])
    assert len(flat) == 3
    assert all("Topics" not in t for t in flat)


# ── 引擎选择（HTML 兜底提供者） ──

def test_engines_for_repo_query():
    assert "github" in ScrapeProviders._engines_for("github langchain")
    assert "github" in ScrapeProviders._engines_for("openai/tiktoken")
    assert "github" not in ScrapeProviders._engines_for("天气 北京")


# ── URL 规范化与去重 ──

def test_normalize_url():
    f = WebSearchFunc._normalize_url
    # 主机名大小写不敏感，路径保留原大小写（RFC 语义）
    assert f("https://www.Example.com/Path/") == "example.com/Path"
    assert f("HTTPS://Example.com/Path#frag") == "example.com/Path"
    assert f("https://example.com/a") != f("https://example.com/b")
    assert f("") == ""


def test_dedupe_keeps_first_and_caps():
    results = [
        {"title": "A", "link": "https://x.com/a", "snippet": "s1"},
        {"title": "A2", "link": "https://www.x.com/a", "snippet": "dup"},
        {"title": "B", "link": "https://x.com/b", "snippet": "s2"},
        {"title": "C", "link": "", "snippet": "s3"},
        {"title": "c", "link": "", "snippet": "dup-title"},
    ]
    out = WebSearchFunc._dedupe(results, 10)
    assert len(out) == 3
    assert out[0]["title"] == "A"
    assert out[1]["title"] == "B"
    assert out[2]["title"] == "C"

    capped = WebSearchFunc._dedupe(
        [{"title": f"t{i}", "link": f"https://x.com/{i}", "snippet": ""} for i in range(20)],
        10,
    )
    assert len(capped) == 10


# ── 输出格式 ──

def test_format_result_with_answer():
    sources = [{"title": "标题", "link": "https://example.com", "snippet": "摘要"}]
    text = WebSearchFunc._format_result("q", "这是答案", sources)
    assert "答案: 这是答案" in text
    assert "1. [标题](https://example.com)" in text
    assert "摘要" in text


def test_format_result_without_answer():
    sources = [{"title": "标题", "link": "https://example.com", "snippet": "摘要"}]
    text = WebSearchFunc._format_result("q", None, sources)
    assert not text.startswith("答案")
    assert "1. [标题](https://example.com)" in text


def test_format_result_truncates_snippet():
    long = "x" * 300
    text = WebSearchFunc._format_result(
        "q", None, [{"title": "t", "link": "https://e.com", "snippet": long}]
    )
    assert "x" * 201 not in text


# ── 提供者合并 + 回答合成（集成） ──

async def _no_synth(query, sources):
    return None


def test_search_merges_answer_and_dedupes_sources(monkeypatch):
    class FakeDDG:
        async def search(self, query, client=None, max_results=10):
            return SearchResult(
                answer="答案是 A",
                sources=[{"title": "s1", "link": "https://a.com/1", "snippet": "snip"}],
            )

    class FakeScrape:
        async def search(self, query, client=None, max_results=10):
            return SearchResult(
                sources=[
                    {"title": "s1-dup", "link": "https://a.com/1", "snippet": "dup"},
                    {"title": "s2", "link": "https://b.com/2", "snippet": "snip2"},
                ]
            )

    monkeypatch.setattr(WebSearchFunc, "PROVIDERS", (FakeDDG, FakeScrape))
    monkeypatch.setattr("src.tools.web_search._synthesize_answer", _no_synth)
    tool = WebSearchFunc(query="q")
    result = asyncio.run(tool.execute())

    # 合成失败时降级为提供者回答
    assert "答案: 答案是 A" in result
    assert "[s1](https://a.com/1)" in result
    assert "s1-dup" not in result
    assert "[s2](https://b.com/2)" in result


def test_search_synthesized_answer_takes_precedence(monkeypatch):
    class FakeDDG:
        async def search(self, query, client=None, max_results=10):
            return SearchResult(
                answer="提供者答案",
                sources=[{"title": "s1", "link": "https://a.com/1", "snippet": "snip"}],
            )

    async def fake_synth(query, sources):
        return "LLM 合成答案"

    monkeypatch.setattr(WebSearchFunc, "PROVIDERS", (FakeDDG,))
    monkeypatch.setattr("src.tools.web_search._synthesize_answer", fake_synth)
    tool = WebSearchFunc(query="q")
    result = asyncio.run(tool.execute())

    assert "答案: LLM 合成答案" in result
    assert "提供者答案" not in result


def test_search_no_answer_degrades_to_sources_only(monkeypatch):
    class FakeScrape:
        async def search(self, query, client=None, max_results=10):
            return SearchResult(
                sources=[{"title": "s1", "link": "https://a.com/1", "snippet": "snip"}]
            )

    monkeypatch.setattr(WebSearchFunc, "PROVIDERS", (FakeScrape,))
    monkeypatch.setattr("src.tools.web_search._synthesize_answer", _no_synth)
    tool = WebSearchFunc(query="q")
    result = asyncio.run(tool.execute())

    assert not result.startswith("答案")
    assert "[s1](https://a.com/1)" in result


def test_search_no_results_returns_error(monkeypatch):
    class FakeEmpty:
        async def search(self, query, client=None, max_results=10):
            return SearchResult()

    monkeypatch.setattr(WebSearchFunc, "PROVIDERS", (FakeEmpty,))
    monkeypatch.setattr("src.tools.web_search._synthesize_answer", _no_synth)
    tool = WebSearchFunc(query="q")
    result = asyncio.run(tool.execute())
    assert result.startswith("(")


# ── 回答合成 ──

def test_build_synthesis_messages():
    from src.tools.web_search import _build_synthesis_messages

    messages = _build_synthesis_messages(
        "什么是 pytest",
        [{"title": "标题", "link": "https://example.com", "snippet": "摘要内容"}],
    )
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "问题: 什么是 pytest" in user
    assert "[1] 标题" in user
    assert "URL: https://example.com" in user
    assert "摘要: 摘要内容" in user


def test_build_synthesis_messages_truncates_snippet():
    from src.tools.web_search import _build_synthesis_messages

    long = "x" * 400
    messages = _build_synthesis_messages(
        "q", [{"title": "t", "link": "https://e.com", "snippet": long}]
    )
    assert "x" * 301 not in messages[1]["content"]


def test_synthesize_answer_returns_none_without_api_key(monkeypatch):
    from src.tools.web_search import _synthesize_answer

    monkeypatch.delenv("CHAT_API_KEY", raising=False)
    result = asyncio.run(
        _synthesize_answer("q", [{"title": "t", "link": "https://e.com", "snippet": "s"}])
    )
    assert result is None


def test_synthesize_answer_returns_none_without_sources(monkeypatch):
    from src.tools.web_search import _synthesize_answer

    monkeypatch.setenv("CHAT_API_KEY", "dummy-key")
    result = asyncio.run(_synthesize_answer("q", []))
    assert result is None


def test_synthesize_answer_uses_model_response(monkeypatch):
    from src.tools.web_search import _synthesize_answer

    monkeypatch.setenv("CHAT_API_KEY", "dummy-key")

    async def fake_call(messages, **kwargs):
        return ("", "这是合成回答 [1]", {"input": 1, "output": 1}, [])

    monkeypatch.setattr("src.api.model_async.call_model_sync_async", fake_call)
    result = asyncio.run(
        _synthesize_answer("q", [{"title": "t", "link": "https://e.com", "snippet": "s"}])
    )
    assert result == "这是合成回答 [1]"


def test_synthesize_answer_failure_returns_none(monkeypatch):
    from src.tools.web_search import _synthesize_answer

    monkeypatch.setenv("CHAT_API_KEY", "dummy-key")

    async def fake_fail(messages, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr("src.api.model_async.call_model_sync_async", fake_fail)
    result = asyncio.run(
        _synthesize_answer("q", [{"title": "t", "link": "https://e.com", "snippet": "s"}])
    )
    assert result is None


def test_synthesize_answer_interrupted_returns_none(monkeypatch):
    from src.tools.web_search import _synthesize_answer

    monkeypatch.setenv("CHAT_API_KEY", "dummy-key")

    async def fake_interrupted(messages, **kwargs):
        return ("", "(已中断)", {"input": 0, "output": 0}, [])

    monkeypatch.setattr("src.api.model_async.call_model_sync_async", fake_interrupted)
    result = asyncio.run(
        _synthesize_answer("q", [{"title": "t", "link": "https://e.com", "snippet": "s"}])
    )
    assert result is None


def test_synthesize_answer_truncates_long_response(monkeypatch):
    from src.tools.web_search import _synthesize_answer

    monkeypatch.setenv("CHAT_API_KEY", "dummy-key")

    async def fake_long(messages, **kwargs):
        return ("", "x" * 1000, {"input": 1, "output": 1000}, [])

    monkeypatch.setattr("src.api.model_async.call_model_sync_async", fake_long)
    result = asyncio.run(
        _synthesize_answer("q", [{"title": "t", "link": "https://e.com", "snippet": "s"}])
    )
    assert len(result) == 603  # 600 + "..."
    assert result.endswith("...")


# ── 错误路径 ──

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
