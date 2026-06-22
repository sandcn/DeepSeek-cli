"""测试 WebSearchFunc — 网页搜索 & 内容获取工具

测试策略
--------
- 核心逻辑（构造URL、参数验证、schema）不依赖网络，直接测试
- execute 方法 mock httpx.AsyncClient 和 parser，不发起真实网络请求
- 每个测试类关注一个概念，每个测试方法覆盖单一场景
- 遵循 Arrange/Act/Assert 模式
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.tools.web_search import WebSearchFunc, _USER_AGENTS


# ═══════════════════════════════════════════════════════════════════════════
# 共享清理（避免 _shared_client 跨测试污染）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_shared_client():
    """每个测试用例前后重置共享客户端（防止跨测试污染）"""
    WebSearchFunc._shared_client = None
    yield
    WebSearchFunc._shared_client = None


# ═══════════════════════════════════════════════════════════════════════════
# 1. __init__ 参数验证
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchInit:
    """__init__ 参数验证"""

    def test_default_engine_is_baidu(self):
        f = WebSearchFunc(query="test")
        assert f.engine == "baidu"

    def test_default_mode_is_search(self):
        f = WebSearchFunc(query="test")
        assert f.mode == "search"

    def test_default_num_results_is_10(self):
        f = WebSearchFunc(query="test")
        assert f.num_results == 10

    def test_default_time_range_is_any(self):
        f = WebSearchFunc(query="test")
        assert f.time_range == "any"

    def test_invalid_mode_falls_back_to_search(self):
        f = WebSearchFunc(query="test", mode="invalid")
        assert f.mode == "search"

    def test_invalid_engine_falls_back_to_baidu(self):
        f = WebSearchFunc(query="test", engine="google")
        assert f.engine == "baidu"

    def test_invalid_time_range_falls_back_to_any(self):
        f = WebSearchFunc(query="test", time_range="decade")
        assert f.time_range == "any"

    def test_fetch_mode_preserved(self):
        f = WebSearchFunc(query="https://example.com", mode="fetch")
        assert f.mode == "fetch"

    def test_num_results_clamped_min_1(self):
        f = WebSearchFunc(query="test", num_results=0)
        assert f.num_results == 1

    def test_num_results_clamped_max_20(self):
        f = WebSearchFunc(query="test", num_results=100)
        assert f.num_results == 20

    def test_num_results_normal_value(self):
        f = WebSearchFunc(query="test", num_results=7)
        assert f.num_results == 7

    def test_time_range_week_accepted(self):
        f = WebSearchFunc(query="test", time_range="week")
        assert f.time_range == "week"

    def test_engine_bing_accepted(self):
        f = WebSearchFunc(query="test", engine="bing")
        assert f.engine == "bing"


# ═══════════════════════════════════════════════════════════════════════════
# 2. from_args 参数解析
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchFromArgs:
    """from_args 参数解析"""

    def test_query_only(self):
        f = WebSearchFunc.from_args({"query": "hello"})
        assert f.query == "hello"
        assert f.mode == "search"
        assert f.engine == "baidu"
        assert f.num_results == 10
        assert f.time_range == "any"

    def test_all_params(self):
        f = WebSearchFunc.from_args({
            "query": "test",
            "mode": "fetch",
            "engine": "bing",
            "num_results": 5,
            "time_range": "week",
        })
        assert f.query == "test"
        assert f.mode == "fetch"
        assert f.engine == "bing"
        assert f.num_results == 5
        assert f.time_range == "week"

    def test_extra_params_ignored(self):
        f = WebSearchFunc.from_args({"query": "test", "extra": True})
        assert f.query == "test"
        assert f.num_results == 10  # 默认值

    def test_partial_params(self):
        f = WebSearchFunc.from_args({
            "query": "search term",
            "engine": "bing",
            "num_results": 15,
        })
        assert f.query == "search term"
        assert f.engine == "bing"
        assert f.num_results == 15
        assert f.mode == "search"       # 默认
        assert f.time_range == "any"    # 默认


# ═══════════════════════════════════════════════════════════════════════════
# 3. to_tool_schema schema 结构
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchSchema:
    """to_tool_schema schema 结构"""

    def test_schema_top_level_structure(self):
        schema = WebSearchFunc.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "web_search"
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]

    def test_schema_has_all_parameters(self):
        props = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]
        assert set(props.keys()) == {"query", "mode", "engine", "num_results", "time_range"}

    def test_schema_required_fields(self):
        required = WebSearchFunc.to_tool_schema()["function"]["parameters"]["required"]
        assert required == ["query"]

    def test_schema_query_type(self):
        props = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]
        assert props["query"]["type"] == "string"

    def test_schema_mode_enum_and_default(self):
        props = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]
        assert props["mode"]["enum"] == ["search", "fetch"]
        assert props["mode"]["default"] == "search"

    def test_schema_engine_enum(self):
        props = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]
        assert props["engine"]["enum"] == ["baidu", "bing", "github"]
        assert props["engine"]["default"] == "baidu"

    def test_schema_num_results_default_and_range(self):
        props = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]
        assert props["num_results"]["default"] == 10
        assert props["num_results"]["type"] == "integer"

    def test_schema_time_range_enum(self):
        props = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]
        expected = list(WebSearchFunc.TIME_RANGES.keys())
        assert props["time_range"]["enum"] == expected
        assert props["time_range"]["default"] == "any"

    def test_schema_engine_description_contains_labels(self):
        description = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]["engine"]["description"]
        assert "百度" in description
        assert "必应" in description
        assert "GitHub" in description
        assert "baidu" in description
        assert "bing" in description
        assert "github" in description

    def test_schema_time_range_description_contains_labels(self):
        description = WebSearchFunc.to_tool_schema()["function"]["parameters"]["properties"]["time_range"]["description"]
        assert "不限" in description
        assert "过去一周" in description


# ═══════════════════════════════════════════════════════════════════════════
# 4. display_params 参数摘要
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchDisplayParams:
    """display_params 参数摘要"""

    def test_search_mode_default_shows_baidu(self):
        result = WebSearchFunc.display_params({"query": "test"})
        assert "[百度]" in result
        assert "test" in result

    def test_search_mode_bing(self):
        result = WebSearchFunc.display_params({"query": "test", "engine": "bing"})
        assert "[必应]" in result
        assert "test" in result

    def test_search_mode_github(self):
        result = WebSearchFunc.display_params({"query": "async python", "engine": "github"})
        assert "[GitHub]" in result
        assert "async" in result
        assert "python" in result

    def test_search_mode_with_time_range_day(self):
        result = WebSearchFunc.display_params({
            "query": "test",
            "engine": "baidu",
            "time_range": "day",
        })
        assert "[百度|过去24小时]" in result
        assert "test" in result

    def test_search_mode_with_time_range_week(self):
        result = WebSearchFunc.display_params({
            "query": "test",
            "engine": "bing",
            "time_range": "week",
        })
        assert "[必应|过去一周]" in result

    def test_fetch_mode_shows_url(self):
        result = WebSearchFunc.display_params({
            "query": "https://example.com/page",
            "mode": "fetch",
        })
        assert "[获取网页]" in result
        assert "https://example.com/page" in result

    def test_truncation_search(self):
        long_query = "a" * 200
        result = WebSearchFunc.display_params({"query": long_query}, max_len=50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_truncation_fetch(self):
        long_url = "https://example.com/" + "b" * 200
        result = WebSearchFunc.display_params({"query": long_url, "mode": "fetch"}, max_len=50)
        assert len(result) <= 50
        assert result.endswith("...")

    def test_time_range_any_omitted_from_display(self):
        """time_range='any' 时不显示时间标签"""
        result = WebSearchFunc.display_params({
            "query": "test",
            "engine": "baidu",
            "time_range": "any",
        })
        assert "[百度]" in result
        assert "|" not in result  # 不包含时间标签分隔符


# ═══════════════════════════════════════════════════════════════════════════
# 5. _build_search_url URL 构建
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchBuildUrl:
    """_build_search_url URL 构建"""

    def test_baidu_url_encoded_query(self):
        url = WebSearchFunc._build_search_url("baidu", "hello world", "any")
        assert "www.baidu.com/s?wd=" in url
        assert "hello" in url
        assert "world" in url
        assert "%20" in url or "+" in url  # URL encoded

    def test_bing_url(self):
        url = WebSearchFunc._build_search_url("bing", "test", "any")
        assert "www.bing.com/search?q=" in url
        assert "test" in url
        assert "setlang=zh-cn" in url
        assert "cc=cn" in url

    def test_bing_time_range_day(self):
        url = WebSearchFunc._build_search_url("bing", "hello", "day")
        assert "freshness=Day" in url

    def test_bing_time_range_week(self):
        url = WebSearchFunc._build_search_url("bing", "hello", "week")
        assert "freshness=Week" in url

    def test_bing_time_range_month(self):
        url = WebSearchFunc._build_search_url("bing", "hello", "month")
        assert "freshness=Month" in url

    def test_bing_time_range_year(self):
        url = WebSearchFunc._build_search_url("bing", "hello", "year")
        assert "freshness=Year" in url

    def test_baidu_time_range_no_suffix(self):
        """百度不支持 URL 参数时间筛选"""
        url = WebSearchFunc._build_search_url("baidu", "hello", "day")
        assert "freshness" not in url
        assert "wd=hello" in url

    def test_any_time_range_no_suffix(self):
        url = WebSearchFunc._build_search_url("bing", "hello", "any")
        assert "freshness" not in url

    def test_encoded_query_special_chars(self):
        url = WebSearchFunc._build_search_url("baidu", "a&b=c", "any")
        assert "%26" in url  # & encoded

    def test_github_url(self):
        url = WebSearchFunc._build_search_url("github", "async python", "any")
        assert "github.com/search" in url
        assert "q=async+python" in url or "q=async%20python" in url
        assert "type=repositories" in url

    def test_github_time_range_ignored(self):
        """GitHub 不支持 time_range，any 之外不应追加时间参数"""
        url_default = WebSearchFunc._build_search_url("github", "test", "any")
        url_day = WebSearchFunc._build_search_url("github", "test", "day")
        assert url_default == url_day  # time_range 不影响 GitHub URL


# ═══════════════════════════════════════════════════════════════════════════
# 6. _random_ua User-Agent 轮换
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchRandomUA:
    """_random_ua User-Agent 轮换"""

    def test_returns_string(self):
        ua = WebSearchFunc._random_ua()
        assert isinstance(ua, str)
        assert len(ua) > 20

    def test_contains_mozilla(self):
        ua = WebSearchFunc._random_ua()
        assert "Mozilla" in ua
        assert "Chrome" in ua

    def test_from_pool(self):
        ua = WebSearchFunc._random_ua()
        assert ua in _USER_AGENTS

    def test_different_calls_may_differ(self):
        """多次调用可能返回不同的 UA（随机性）"""
        uas = {WebSearchFunc._random_ua() for _ in range(20)}
        # 至少出现过 2 种不同的 UA（概率极高）
        assert len(uas) >= 2


# ═══════════════════════════════════════════════════════════════════════════
# 7. 配置完整性
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchConfig:
    """ENGINES / TIME_RANGES / ENGINE_TIME_PARAMS 配置完整性"""

    def test_engines_contains_all(self):
        assert "baidu" in WebSearchFunc.ENGINES
        assert "bing" in WebSearchFunc.ENGINES
        assert "github" in WebSearchFunc.ENGINES

    def test_engines_have_required_keys(self):
        for engine in ("baidu", "bing", "github"):
            for key in ("label", "base_url", "referer"):
                assert key in WebSearchFunc.ENGINES[engine], f"{engine} missing key {key}"

    def test_engines_labels(self):
        assert WebSearchFunc.ENGINES["baidu"]["label"] == "百度"
        assert WebSearchFunc.ENGINES["bing"]["label"] == "必应"
        assert WebSearchFunc.ENGINES["github"]["label"] == "GitHub"

    def test_time_ranges_contains_all(self):
        expected = {"any", "day", "week", "month", "year"}
        assert set(WebSearchFunc.TIME_RANGES.keys()) == expected

    def test_time_range_labels(self):
        assert WebSearchFunc.TIME_RANGES["any"] == "不限"
        assert WebSearchFunc.TIME_RANGES["day"] == "过去24小时"
        assert WebSearchFunc.TIME_RANGES["week"] == "过去一周"

    def test_engine_time_params_has_all_engines(self):
        assert "bing" in WebSearchFunc.ENGINE_TIME_PARAMS
        assert "baidu" in WebSearchFunc.ENGINE_TIME_PARAMS
        assert "github" in WebSearchFunc.ENGINE_TIME_PARAMS

    def test_bing_time_params_all_ranges(self):
        bing = WebSearchFunc.ENGINE_TIME_PARAMS["bing"]
        for r in ("day", "week", "month", "year"):
            assert r in bing
            assert "freshness=" in bing[r]

    def test_baidu_time_params_empty(self):
        assert WebSearchFunc.ENGINE_TIME_PARAMS["baidu"] == {}

    def test_github_time_params_empty(self):
        assert WebSearchFunc.ENGINE_TIME_PARAMS["github"] == {}


# ═══════════════════════════════════════════════════════════════════════════
# 8. execute（搜索模式）
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchExecuteSearch:
    """execute 搜索模式（mock httpx + parser）"""

    async def test_search_success(self):
        """搜索成功返回格式化结果"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>dummy</html>"
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.importlib.import_module') as mock_import:
                mock_parser = MagicMock()
                mock_parser.parse.return_value = [
                    {"title": "First Result", "link": "https://a.com", "abstract": "Description A"},
                    {"title": "Second Result", "link": "https://b.com", "abstract": "Description B"},
                ]
                mock_import.return_value = mock_parser

                f = WebSearchFunc(query="test", engine="baidu")
                result = await f.execute()

                assert "搜索结果" in result
                assert "First Result" in result
                assert "Second Result" in result
                assert "https://a.com" in result
                assert "Description A" in result
                assert "(2条)" in result or "2条" in result

    async def test_search_no_results(self):
        """搜索无结果"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>dummy</html>"
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.importlib.import_module') as mock_import:
                mock_parser = MagicMock()
                mock_parser.parse.return_value = []
                mock_import.return_value = mock_parser

                f = WebSearchFunc(query="nonexistent", engine="baidu")
                result = await f.execute()

                assert result.startswith("(")
                assert "未找到结果" in result

    async def test_search_status_not_200(self):
        """搜索返回非 200 状态码"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            f = WebSearchFunc(query="test", engine="baidu")
            result = await f.execute()

            assert result.startswith("(")
            assert "百度" in result
            assert "403" in result

    async def test_search_status_500(self):
        """搜索返回 500 状态码"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 500
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            f = WebSearchFunc(query="test", engine="bing")
            result = await f.execute()

            assert result.startswith("(")
            assert "必应" in result
            assert "500" in result

    async def test_search_timeout(self):
        """搜索超时"""
        mock_client = AsyncMock()
        mock_client.get.side_effect = httpx.TimeoutException("Connection timed out")

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            f = WebSearchFunc(query="test", engine="baidu")
            result = await f.execute()

            assert result.startswith("(")
            assert "超时" in result
            assert "百度" in result

    async def test_search_parser_module_not_found(self):
        """parser 模块加载异常"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>dummy</html>"
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.importlib.import_module',
                       side_effect=ModuleNotFoundError("No module named 'parsers.baidu'")):
                f = WebSearchFunc(query="test", engine="baidu")
                result = await f.execute()

                assert result.startswith("(")
                assert "解析器加载失败" in result

    async def test_search_other_exception(self):
        """搜索过程中其他异常"""
        mock_client = AsyncMock()
        mock_client.get.side_effect = RuntimeError("unexpected failure")

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            f = WebSearchFunc(query="test", engine="baidu")
            result = await f.execute()

            assert result.startswith("(")
            assert "百度" in result
            assert "失败" in result
            assert "unexpected failure" in result

    async def test_search_github_engine(self):
        """使用 GitHub 引擎搜索"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>result</html>"
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.importlib.import_module') as mock_import:
                mock_parser = MagicMock()
                mock_parser.parse.return_value = [
                    {"title": "owner/repo", "link": "https://github.com/owner/repo", "abstract": "A great repo"},
                ]
                mock_import.return_value = mock_parser

                f = WebSearchFunc(query="async python", engine="github")
                result = await f.execute()

                assert "owner/repo" in result
                assert "https://github.com/owner/repo" in result
                assert "A great repo" in result
                # 验证调用了正确的引擎配置
                call_url = mock_client.get.call_args[0][0]
                assert "github.com/search" in call_url
                assert "type=repositories" in call_url

    async def test_search_bing_engine(self):
        """使用必应引擎搜索"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>result</html>"
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.importlib.import_module') as mock_import:
                mock_parser = MagicMock()
                mock_parser.parse.return_value = [
                    {"title": "Bing Result", "link": "https://bing.com/r", "abstract": "From Bing"},
                ]
                mock_import.return_value = mock_parser

                f = WebSearchFunc(query="hello", engine="bing")
                result = await f.execute()

                assert "Bing Result" in result
                # 验证调用了正确的引擎配置
                call_url = mock_client.get.call_args[0][0]
                assert "bing.com" in call_url

    async def test_search_abstract_truncated(self):
        """摘要超过 200 字符时截断"""
        mock_client = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html>dummy</html>"
        mock_client.get.return_value = mock_response

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.importlib.import_module') as mock_import:
                mock_parser = MagicMock()
                mock_parser.parse.return_value = [
                    {
                        "title": "Long Abstract",
                        "link": "https://a.com",
                        "abstract": "A" * 300,
                    },
                ]
                mock_import.return_value = mock_parser

                f = WebSearchFunc(query="test", engine="baidu")
                result = await f.execute()

                assert "..." in result  # 截断标记
                # 摘要应被截断到 200 + "..." = 203
                assert "A" * 200 in result


# ═══════════════════════════════════════════════════════════════════════════
# 9. execute（fetch 模式）
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchExecuteFetch:
    """execute fetch 模式（mock page_fetcher）"""

    async def test_fetch_success(self):
        """fetch 模式成功获取网页"""
        mock_client = AsyncMock()
        fetch_result = {
            "title": "Example Page",
            "url": "https://example.com/page",
            "domain": "example.com",
            "date": "2024-06-15",
            "body": "This is the page content body.",
        }

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.fetch_page', new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = fetch_result

                f = WebSearchFunc(query="https://example.com/page", mode="fetch")
                result = await f.execute()

                assert "Example Page" in result
                assert "example.com" in result
                assert "2024-06-15" in result
                assert "This is the page content body" in result

    async def test_fetch_with_error_in_result(self):
        """fetch 返回 error 字段"""
        mock_client = AsyncMock()

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.fetch_page', new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = {"error": "(fetch失败: URL为空)", "url": ""}

                f = WebSearchFunc(query="", mode="fetch")
                result = await f.execute()

                assert "URL为空" in result

    async def test_fetch_timeout(self):
        """fetch 超时"""
        mock_client = AsyncMock()

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.fetch_page', new_callable=AsyncMock) as mock_fetch:
                mock_fetch.side_effect = httpx.TimeoutException("timeout")

                f = WebSearchFunc(query="https://example.com", mode="fetch")
                result = await f.execute()

                assert "超时" in result
                assert "https://example.com" in result

    async def test_fetch_generic_error(self):
        """fetch 发生其他异常"""
        mock_client = AsyncMock()

        with patch.object(WebSearchFunc, '_get_client', return_value=mock_client):
            with patch('src.tools.web_search.fetch_page', new_callable=AsyncMock) as mock_fetch:
                mock_fetch.side_effect = RuntimeError("connection refused")

                f = WebSearchFunc(query="https://example.com", mode="fetch")
                result = await f.execute()

                assert "获取网页失败" in result
                assert "connection refused" in result


# ═══════════════════════════════════════════════════════════════════════════
# 10. 共享 AsyncClient 管理
# ═══════════════════════════════════════════════════════════════════════════

class TestWebSearchClient:
    """_get_client / close_client 管理"""

    async def test_get_client_lazy_init(self):
        """_get_client 懒初始化：首次调用才创建实例（异步安全）"""
        assert WebSearchFunc._shared_client is None
        client = await WebSearchFunc._get_client()
        assert client is not None
        assert isinstance(client, httpx.AsyncClient)
        assert WebSearchFunc._shared_client is client

    async def test_get_client_reuses_connection(self):
        """连接池复用：多次调用返回同一实例"""
        client1 = await WebSearchFunc._get_client()
        client2 = await WebSearchFunc._get_client()
        assert client1 is client2

    async def test_close_client_releases(self):
        """close_client 释放资源后置 None"""
        client = await WebSearchFunc._get_client()
        assert WebSearchFunc._shared_client is not None
        await WebSearchFunc.close_client()
        assert WebSearchFunc._shared_client is None

    async def test_close_client_idempotent(self):
        """close_client 可幂等调用（已关闭后再次调用不报错）"""
        await WebSearchFunc.close_client()
        await WebSearchFunc.close_client()  # 不应抛异常
        assert WebSearchFunc._shared_client is None

    async def test_get_client_after_close(self):
        """close 后重新 _get_client 创建新实例"""
        client1 = await WebSearchFunc._get_client()
        await WebSearchFunc.close_client()
        client2 = await WebSearchFunc._get_client()
        assert client2 is not None
        assert client2 is not client1
