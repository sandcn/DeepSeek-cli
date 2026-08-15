"""
search_providers — 搜索提供者层

对齐内置 web_search 的内部架构：web_search 工具本身不解析任何搜索引擎
HTML，只通过提供者获取结构化结果 — 可选的摘要回答 + 来源列表
（每条含 title / link / snippet）。

提供者：
  - DuckDuckGoProvider: 免密钥结构化 JSON API（Instant Answer），
    原生返回 Answer/Abstract（可选回答）与 RelatedTopics（来源+片段），
    与“可选回答 + 来源列表”契约一致
  - ScrapeProviders: 聚合必应/百度（仓库类查询追加 GitHub）的 HTML
    解析结果作为兜底来源（answer=None）——DDG API 不可达的网络上
    保障可用性

共享 AsyncClient 由本模块托管（web_search / web_fetch 共用连接池）。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import random
import re
from dataclasses import field
from typing import Optional
from urllib.parse import quote_plus, urlparse

import httpx

from src._compat import dataclass
from ._constants import WEB_USER_AGENTS as _USER_AGENTS

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  结构化搜索结果
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class SearchResult:
    """结构化搜索结果 — 与内置 web_search 的返回契约一致。

    Attributes:
        answer: 可选的摘要回答（无则为 None）
        sources: 来源列表，每条 {title, link, snippet}
    """
    answer: Optional[str] = None
    sources: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
#  共享 AsyncClient（web_search / web_fetch 共用连接池）
# ═══════════════════════════════════════════════════════════

_shared_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def get_shared_client() -> httpx.AsyncClient:
    """获取共享 AsyncClient 实例（连接池复用，懒初始化，异步安全）

    如果客户端已关闭（如被外部调用 shutdown_client），自动重新创建。
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _client_lock:
            if _shared_client is None or _shared_client.is_closed:
                limits = httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=20,
                    keepalive_expiry=60.0,
                )
                _shared_client = httpx.AsyncClient(
                    timeout=15,
                    follow_redirects=True,
                    limits=limits,
                )
    return _shared_client


async def shutdown_client() -> None:
    """关闭共享 AsyncClient，释放连接池资源（应用退出时调用）"""
    global _shared_client
    if _shared_client is not None:
        async with _client_lock:
            if _shared_client is not None:
                await _shared_client.aclose()
                _shared_client = None


def _random_ua() -> str:
    """从 User-Agent 池中随机选取一个"""
    return random.choice(_USER_AGENTS)


# ═══════════════════════════════════════════════════════════
#  提供者：DuckDuckGo Instant Answer（结构化 JSON，免密钥）
# ═══════════════════════════════════════════════════════════

_DDG_API = "https://api.duckduckgo.com/"
# DDG 在部分网络不可达（如无代理的国内网络）。用短连接超时（3s）快速失败，
# 其余阶段 5s；避免每次搜索都被拖慢，失败后由 HTML 解析提供者兜底。
_DDG_TIMEOUT = httpx.Timeout(5.0, connect=3.0)


class DuckDuckGoProvider:
    """免密钥结构化搜索 API 提供者。

    响应原生包含 Answer/Abstract（可选回答）与 RelatedTopics
    （来源列表：Text 片段 + FirstURL 链接），与“可选回答 + 来源列表”
    契约一致。任何失败都返回空 SearchResult，由调用方降级到其他提供者。
    """

    label = "DuckDuckGo"

    async def search(self, query: str, client: Optional[httpx.AsyncClient] = None,
                     max_results: int = 10) -> SearchResult:
        client = client or await get_shared_client()
        try:
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
                "kl": "cn-zh",
            }
            resp = await client.get(
                _DDG_API,
                params=params,
                timeout=_DDG_TIMEOUT,
                headers={
                    "User-Agent": _random_ua(),
                    "Accept": "application/json",
                },
            )
            if resp.status_code != 200:
                _logger.warning("DuckDuckGo 返回状态码: %s", resp.status_code)
                return SearchResult()
            result = self._parse(resp.json())
            result.sources = result.sources[:max_results]
            return result
        except Exception as e:
            _logger.warning("DuckDuckGo 搜索失败: %s: %s", type(e).__name__, e)
            return SearchResult()

    @staticmethod
    def _parse(data: dict) -> SearchResult:
        """解析 DDG Instant Answer JSON → 结构化结果"""
        result = SearchResult()

        abstract = (data.get("AbstractText") or "").strip()
        answer = (data.get("Answer") or "").strip()

        # 可选回答：优先 Answer（即时答案），其次 AbstractText（摘要段落）
        if answer:
            result.answer = answer
        elif abstract:
            result.answer = abstract

        # 主来源：Abstract 的出处（回答的 provenance）
        if abstract and data.get("AbstractURL"):
            result.sources.append({
                "title": data.get("Heading") or data.get("AbstractSource") or "来源",
                "link": data["AbstractURL"],
                "snippet": abstract,
            })

        # RelatedTopics：可能嵌套 Topics 分组
        for topic in DuckDuckGoProvider._flatten_topics(data.get("RelatedTopics") or []):
            text = (topic.get("Text") or "").strip()
            link = (topic.get("FirstURL") or "").strip()
            if not text or not link:
                continue
            result.sources.append({
                "title": topic.get("Name") or _title_from_url(link),
                "link": link,
                "snippet": text,
            })

        return result

    @staticmethod
    def _flatten_topics(topics: list) -> list:
        """DDG RelatedTopics 可能包含嵌套的 Topics 分组，展平为一层"""
        flat: list = []
        for item in topics:
            if isinstance(item, dict) and "Topics" in item:
                flat.extend(item.get("Topics") or [])
            else:
                flat.append(item)
        return flat


def _title_from_url(url: str) -> str:
    """从 URL 推导来源标题（缺省时用域名）"""
    try:
        parsed = urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


# ═══════════════════════════════════════════════════════════
#  提供者：搜索引擎 HTML 解析聚合（兜底，无回答）
# ═══════════════════════════════════════════════════════════

class ScrapeProviders:
    """聚合必应/百度（仓库类查询追加 GitHub）的 HTML 解析结果。

    DuckDuckGo 结构化 API 不可达时的兜底来源；仅提供来源列表
    （answer=None）。单个引擎失败不影响整体。
    """

    label = "网页"

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

    # 疑似仓库查询：包含 github 关键词或形如 owner/repo
    _GITHUB_REPO_QUERY_RE = re.compile(r"^\s*[\w.-]+/[\w.-]+\s*$")

    @classmethod
    def _engines_for(cls, query: str) -> tuple[str, ...]:
        """根据查询选择参与聚合的引擎（默认必应+百度，仓库类查询追加 GitHub）"""
        q = query.strip()
        engines = ["bing", "baidu"]
        if "github" in q.lower() or cls._GITHUB_REPO_QUERY_RE.match(q):
            engines.append("github")
        return tuple(engines)

    async def search(self, query: str, client: Optional[httpx.AsyncClient] = None,
                     max_results: int = 10) -> SearchResult:
        client = client or await get_shared_client()
        engines = self._engines_for(query)
        per_engine = await asyncio.gather(
            *[self._search_engine(engine, query, client, max_results) for engine in engines]
        )
        sources: list = []
        for results in per_engine:
            sources.extend(results)
        return SearchResult(answer=None, sources=sources)

    async def _search_engine(self, engine: str, query: str,
                             client: httpx.AsyncClient, max_results: int) -> list:
        """搜索单个引擎并解析结果。

        返回 [{title, link, snippet}]；任何失败（网络/状态码/解析）都返回
        空列表，保证单引擎故障不影响整体。
        """
        cfg = self.ENGINES[engine]
        try:
            headers = {
                "User-Agent": _random_ua(),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;"
                    "q=0.9,image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": cfg["referer"],
            }
            url = cfg["base_url"].format(query=quote_plus(query))

            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                _logger.warning("%s 搜索返回状态码: %s", cfg["label"], resp.status_code)
                return []

            parser_module = importlib.import_module(
                f".parsers.{engine}", package=__package__
            )
            raw_results = await asyncio.to_thread(parser_module.parse, resp.text, max_results)
            # 解析器输出统一为提供者契约：{title, link, snippet}
            return [
                {
                    "title": r.get("title", ""),
                    "link": r.get("link", ""),
                    "snippet": r.get("abstract", ""),
                }
                for r in (raw_results or [])
            ]
        except Exception as e:
            _logger.warning("%s 搜索失败: %s: %s", cfg["label"], type(e).__name__, e)
            return []
