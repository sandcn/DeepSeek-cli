"""
web_search — 网页搜索 & 内容获取工具

支持两种模式:
  - mode="search"（默认）: 在百度/必应等搜索引擎中搜索关键词，返回标题、链接和摘要
  - mode="fetch": 获取指定 URL 的网页全文内容，自动提取标题/发布时间/正文（去噪音）
"""

from __future__ import annotations

import asyncio
import importlib
import random
from urllib.parse import quote_plus

import httpx

from .base import Func, tool_metadata
from ._constants import WEB_USER_AGENTS as _USER_AGENTS
from .page_fetcher import fetch_page, format_fetch_result
from ..core.constants import GREEN, YELLOW, DIM, RESET
from ..ui._lock import locked_print


@tool_metadata(
    parallel_safe=True,
    requires_network=True,
    requires_terminal=False,
    timeout_estimate=15,
    category="general",
    priority=40,
    tool_category="read",
    description="网页搜索和内容获取",
)
class WebSearchFunc(Func):
    name = "web_search"

    @staticmethod
    def _random_ua():
        """从 User-Agent 池中随机选取一个"""
        return random.choice(_USER_AGENTS)

    ENGINES = {
        "baidu": {
            "label": "百度",
            "base_url": "https://www.baidu.com/s?wd={query}",
            "referer": "https://www.baidu.com/",
        },
        "bing": {
            "label": "必应",
            "base_url": "https://www.bing.com/search?q={query}&setlang=zh-cn&cc=cn",
            "referer": "https://www.bing.com/",
        },
        "github": {
            "label": "GitHub",
            "base_url": "https://github.com/search?q={query}&type=repositories",
            "referer": "https://github.com/",
        },
    }

    TIME_RANGES = {
        "any": "不限",
        "day": "过去24小时",
        "week": "过去一周",
        "month": "过去一月",
        "year": "过去一年",
    }

    ENGINE_TIME_PARAMS = {
        "bing": {
            "day": "&freshness=Day",
            "week": "&freshness=Week",
            "month": "&freshness=Month",
            "year": "&freshness=Year",
        },
        "baidu": {
            # Baidu 暂不支持通过 URL 参数直接筛选时间范围
        },
        "github": {
            # GitHub 搜索暂不支持通过 URL 参数直接筛选时间范围
        },
    }

    # ── 共享 AsyncClient（连接池复用，懒初始化） ──
    _shared_client: httpx.AsyncClient | None = None
    _client_lock = asyncio.Lock()

    @classmethod
    def _build_search_url(cls, engine: str, query: str, time_range: str) -> str:
        """构建搜索引擎 URL，含时间范围参数"""
        cfg = cls.ENGINES[engine]
        url = cfg["base_url"].format(query=quote_plus(query))
        if time_range and time_range != "any":
            time_params = cls.ENGINE_TIME_PARAMS.get(engine, {})
            time_suffix = time_params.get(time_range)
            if time_suffix:
                url += time_suffix
        return url

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        """获取共享 AsyncClient 实例（连接池复用，懒初始化，异步安全）

        如果客户端已关闭（如被外部调用 shutdown），自动重新创建以避免连接泄漏。
        """
        if cls._shared_client is None or cls._shared_client.is_closed:
            async with cls._client_lock:
                if cls._shared_client is None or cls._shared_client.is_closed:
                    limits = httpx.Limits(
                        max_keepalive_connections=5,
                        max_connections=20,
                        keepalive_expiry=60.0,
                    )
                    cls._shared_client = httpx.AsyncClient(
                        timeout=15,
                        follow_redirects=True,
                        limits=limits,
                    )
        return cls._shared_client

    @classmethod
    def to_tool_schema(cls):
        engine_desc = ("搜索引擎，可选: " + ", ".join(
            f"{k}({v['label']})" for k, v in cls.ENGINES.items()
        ) + "。百度(baidu)国内中文搜索最优、必应(bing)两者兼顾、GitHub(github)仓库搜索。仅 mode='search' 时有效。")
        time_range_desc = ("时间范围，可选: " + ", ".join(
            f"{k}({v})" for k, v in cls.TIME_RANGES.items()
        ) + "。注意：百度不支持URL参数筛选时间范围，仅必应有效。仅 mode='search' 时有效。")
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "网页搜索 & 网页内容获取。支持两种模式：\n\n"
                    "【mode='search'（默认）】在百度/必应等搜索引擎中搜索关键词，"
                    "返回标题/链接/摘要。参数：query(必填)、engine、num_results、time_range。\n\n"
                    "【mode='fetch'】获取指定 URL 的网页全文内容，"
                    "自动提取标题、发布时间、正文（去除导航/广告/页脚噪音）。参数：query(填URL)、mode='fetch'。\n\n"
                    "使用建议：当需要了解网页完整内容（如技术文章、文档、新闻）时使用 mode='fetch'；"
                    "当需要查找相关资源时使用 mode='search'。\n\n"
                    "【边界信息】\n"
                    "- 禁止访问内网/私有地址（SSRF防护）\n"
                    "- 仅支持 http/https 协议\n"
                    "- 网络超时：15秒\n"
                    "- 正文最大输出 50000 字符，超出截断\n"
                    "- 自动检测编码（UTF-8/GBK等）\n"
                    "- User-Agent池随机轮换，降低被反爬概率"
                    "\n\n"
                    "【来源追溯（强制）】通过 web_search 获取的代码片段必须标注来源 URL 和验证状态"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词（mode='search'时），或要获取全文的 URL（mode='fetch'时）"
                        },
                        "mode": {
                            "type": "string",
                            "description": "操作模式：'search' 搜索网页（默认），'fetch' 获取指定URL的全文内容",
                            "default": "search",
                            "enum": ["search", "fetch"],
                        },
                        "engine": {
                            "type": "string",
                            "description": engine_desc,
                            "default": "baidu",
                            "enum": list(cls.ENGINES.keys()),
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "返回结果数量，范围1-20，默认10，超出自动裁剪到边界值。仅 mode='search' 时有效。",
                            "default": 10
                        },
                        "time_range": {
                            "type": "string",
                            "description": time_range_desc,
                            "default": "any",
                            "enum": list(cls.TIME_RANGES.keys()),
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        query = arguments.get("query", "")
        mode = arguments.get("mode", "search")
        engine = arguments.get("engine", "baidu")
        time_range = arguments.get("time_range", "any")
        if mode == "fetch":
            s = f"[获取网页] {query}"
        else:
            label = cls.ENGINES.get(engine, {}).get("label", engine)
            time_label = cls.TIME_RANGES.get(time_range, "")
            if time_range and time_range != "any" and time_label:
                s = f"[{label}|{time_label}] {query}"
            else:
                s = f"[{label}] {query}"
        if len(s) > max_len:
            s = s[: max_len - 3] + "..."
        return s

    def __init__(self, query, mode="search", engine="baidu", num_results=10, time_range="any"):
        super().__init__()
        self.query = query
        self.mode = mode if mode in ("search", "fetch") else "search"
        self.engine = engine if engine in self.ENGINES else "baidu"
        self.num_results = min(max(1, num_results), 20)
        self.time_range = time_range if time_range in self.TIME_RANGES else "any"

    async def execute(self) -> str:
        if self.mode == "fetch":
            return await self._fetch_async()
        try:
            return await self._search_async()
        except httpx.TimeoutException:
            label = self.ENGINES[self.engine]["label"]
            return f"({label}搜索超时: {self.query})"
        except ModuleNotFoundError as e:
            label = self.ENGINES[self.engine]["label"]
            return f"({label}搜索解析器加载失败: {e}，请检查 parsers/{self.engine}.py 是否存在)"
        except Exception as e:
            label = self.ENGINES[self.engine]["label"]
            return f"({label}搜索失败: {e})"

    async def _search_async(self):
        """异步搜索，使用 httpx.AsyncClient"""
        cfg = self.ENGINES[self.engine]
        headers = {
            "User-Agent": self._random_ua(),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": cfg["referer"],
        }
        url = self._build_search_url(self.engine, self.query, self.time_range)

        client = await self._get_client()
        resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            return f"({cfg['label']}搜索返回状态码: {resp.status_code})"

        parser_module = importlib.import_module(f'.parsers.{self.engine}', package=__package__)
        results = await asyncio.to_thread(parser_module.parse, resp.text, self.num_results)

        if not results:
            return f"({cfg['label']}搜索未找到结果: {self.query})"

        label = cfg["label"]
        output_lines = [f"{label}搜索结果 ({len(results)}条):"]
        for i, r in enumerate(results, 1):
            output_lines.append(f"\n{i}. {r['title']}")
            if r.get('link'):
                output_lines.append(f"   {r['link']}")
            if r.get('abstract'):
                abstract = r['abstract']
                if len(abstract) > 200:
                    abstract = abstract[:200] + "..."
                output_lines.append(f"   {abstract}")

        return '\n'.join(output_lines)

    async def _fetch_async(self) -> str:
        """获取网页全文"""
        url = self.query.strip()
        try:
            client = await self._get_client()
            result = await fetch_page(url, client=client)
        except httpx.TimeoutException:
            return f"(获取网页超时: {url})"
        except Exception as e:
            return f"(获取网页失败: {e})"

        if "error" in result:
            return result["error"]

        return format_fetch_result(result)

    @classmethod
    async def shutdown(cls):
        """关闭共享 AsyncClient，释放连接池资源（应用退出时调用）"""
        if cls._shared_client is not None:
            async with cls._client_lock:
                if cls._shared_client is not None:
                    await cls._shared_client.aclose()
                    cls._shared_client = None

    close_client = shutdown

    # ── 显示 ──

    async def display(self) -> str:
        if self.mode == "fetch":
            locked_print(f"\n  {GREEN}📄 获取网页: {self.query}{RESET}")
        else:
            cfg = self.ENGINES[self.engine]
            locked_print(f"\n  {GREEN}🔍 {cfg['label']}搜索: {self.query}{RESET}")
        result = await self.execute()

        if result.startswith("("):
            locked_print(f"  {YELLOW}{result}{RESET}")
        else:
            locked_print(f"  {DIM}{result}{RESET}")

        return result

