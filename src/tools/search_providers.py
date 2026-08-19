"""
search_providers — 搜索提供者层（DeepSeek 官方原生搜索）

对齐 DSH @deepseek-ai/dsh-web-search-deepseek 的实现：
  - web_search 工具本身不解析任何搜索引擎 HTML，只通过提供者获取结构化结果
  - DeepSeekSearchProvider 调用 DeepSeek 的 Anthropic 兼容 Messages API
    （POST {base_url}/v1/messages），声明原生服务端工具
    web_search_20250305，由 DeepSeek 模型执行真实联网搜索并返回结构化
    web_search_tool_result 块；每次搜索消耗一个模型回合
  - 摘要片段来自响应 text 块的 citations（url → cited_text，首见生效）：
    web_search_result 条目只携带 url/title/page_age，不含内联摘要
  - 按 URL 精确去重（首见生效）；数量上限由 web_search 工具层裁剪
    （对齐 DSH：seam 在 WebRuntime.search 中执行 maxResults 截断）

错误语义对齐 DSH WebError 的机器可读 code：
  - WEB_PROVIDER_ERROR            提供者/网络/解析失败
  - WEB_PROVIDER_CREDENTIAL_MISSING 未配置 API 密钥

共享 AsyncClient 由本模块托管（web_fetch 复用连接池）；搜索 API 使用独立
客户端（follow_redirects=False，对齐 DSH redirect:"error"，防止密钥随
重定向泄漏）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import field
from typing import Optional

import httpx

from src._compat import dataclass

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
#  结构化搜索结果
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class SearchResult:
    """结构化搜索结果 — 与 DSH 的规范化搜索输出对齐。

    Attributes:
        answer: 可选的提供者回答（DSH 的 content 字段；DeepSeek 提供者恒为
            None，回答由调用方模型基于来源自行组织）
        sources: 来源列表，每条 {title, link, snippet, published_at?}
    """
    answer: Optional[str] = None
    sources: list = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
#  结构化错误（对齐 DSH WebError 的码值语义）
# ═══════════════════════════════════════════════════════════

class WebSearchError(Exception):
    """带机器可读 code 的搜索错误。"""

    def __init__(self, message: str, code: str = "WEB_PROVIDER_ERROR"):
        self.code = code
        super().__init__(message)


# ═══════════════════════════════════════════════════════════
#  共享 AsyncClient（web_fetch 复用连接池）
# ═══════════════════════════════════════════════════════════

_shared_client: Optional[httpx.AsyncClient] = None
# ★ 2026-08-20（稳定性修复）：模块级 asyncio.Lock() 在 import 时依赖事件循环
#   （Python 3.9 get_event_loop）——asyncio.run 之后的同进程内 import 本模块会抛
#   RuntimeError（xdist 全量测试偶发）。改为 None + 首次 async 使用惰性创建。
_client_lock: Optional[asyncio.Lock] = None


def _get_client_lock() -> asyncio.Lock:
    """惰性创建共享客户端锁（仅 async 上下文调用，保证存在运行事件循环）。"""
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def get_shared_client() -> httpx.AsyncClient:
    """获取共享 AsyncClient 实例（连接池复用，懒初始化，异步安全）

    如果客户端已关闭（如被外部调用 shutdown_client），自动重新创建。
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _get_client_lock():
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
        async with _get_client_lock():
            if _shared_client is not None:
                await _shared_client.aclose()
                _shared_client = None


# ═══════════════════════════════════════════════════════════
#  提供者：DeepSeek 官方原生搜索（Anthropic 兼容 Messages API）
# ═══════════════════════════════════════════════════════════

# 对齐 DSH DEEPSEEK_DEFAULT_BASE_URL：搜索走 Anthropic 兼容 Messages API，
# 与 chat-completions 的 base（api.deepseek.com）不同。
DEEPSEEK_ANTHROPIC_DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic/v1"

# 对齐 DSH DEEPSEEK_DEFAULT_MODEL / _DEFAULT_API_VERSION / _DEFAULT_MAX_TOKENS / _DEFAULT_MAX_USES
DEEPSEEK_SEARCH_DEFAULT_MODEL = "deepseek-v4-flash"
DEEPSEEK_SEARCH_API_VERSION = "2023-06-01"
DEEPSEEK_SEARCH_MAX_TOKENS = 4096
DEEPSEEK_SEARCH_MAX_USES = 5

# 搜索 API 专用超时（对齐 DSH 的 30000ms 协作预算）
DEEPSEEK_SEARCH_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# 归属头，随包版本更新
DEEPSEEK_SEARCH_USER_AGENT = "deepseek-cli/2.2.0"

# 环境变量：搜索端点/模型独立覆盖。刻意不复用聊天端点环境变量——
# 搜索与 chat-completions 使用不同的 API 基址（对齐 DSH SEARCH_BASE_URL_ENV）。
SEARCH_BASE_URL_ENV = "DEEPSEEK_SEARCH_BASE_URL"
SEARCH_MODEL_ENV = "DEEPSEEK_SEARCH_MODEL"


def _search_options() -> tuple[str, str]:
    """解析一次搜索的 (base_url, model)，全部已默认化。"""
    base = os.getenv(SEARCH_BASE_URL_ENV) or DEEPSEEK_ANTHROPIC_DEFAULT_BASE_URL
    model = os.getenv(SEARCH_MODEL_ENV) or DEEPSEEK_SEARCH_DEFAULT_MODEL
    return base.rstrip("/"), model


def _resolve_api_key() -> str:
    """读取当前 API 密钥（CHAT_API_KEY 环境变量，与聊天共用）。"""
    from ..config import API_KEY

    key = (API_KEY or "").strip()
    if not key:
        raise WebSearchError(
            "web_search 未配置 API 密钥（CHAT_API_KEY 环境变量），"
            "DeepSeek 官方搜索需要该密钥",
            "WEB_PROVIDER_CREDENTIAL_MISSING",
        )
    return key


def _citation_snippets(blocks: list) -> dict[str, str]:
    """从 text 块的 citations 提取 url → cited_text 映射（首见生效）。

    对齐 DSH citationSnippets：Anthropic 的 web_search_result 条目通常不含
    内联摘要——摘要片段位于独立 text 块的 citations 中，按 url 匹配。
    """
    snippets: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for cite in block.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            url = (cite.get("url") or "").strip()
            text = (cite.get("cited_text") or "").strip()
            if url and text and url not in snippets:
                snippets[url] = text
    return snippets


class DeepSeekSearchProvider:
    """DeepSeek 官方原生搜索提供者（对齐 DSH dsh-web-search-deepseek）。

    每次搜索是一次 Anthropic 兼容 Messages 调用：user 消息要求“对查询执行
    网络搜索”，并声明服务端工具 web_search_20250305（max_uses 限次）。
    响应中的 web_search_tool_result 块即结构化来源；无该块视为提供者错误
    （而非降级到散文抓取）。
    """

    label = "DeepSeek"

    def __init__(self):
        # 搜索 API 专用客户端：独立于 web_fetch 的共享连接池，
        # follow_redirects=False 对齐 DSH redirect:"error"。
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=DEEPSEEK_SEARCH_TIMEOUT,
                follow_redirects=False,
                limits=httpx.Limits(
                    max_keepalive_connections=2,
                    max_connections=10,
                    keepalive_expiry=60.0,
                ),
            )
        return self._client

    async def aclose(self) -> None:
        """关闭搜索 API 客户端，释放连接池资源。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, query: str, client: Optional[httpx.AsyncClient] = None) -> SearchResult:
        """执行一次 DeepSeek 官方原生搜索。

        Args:
            query: 搜索关键词。
            client: 可选注入的 httpx 客户端（测试用）；None 时使用专用客户端。

        Returns:
            SearchResult(answer=None, sources=[{title, link, snippet?, published_at?}])。

        Raises:
            WebSearchError: 密钥缺失 / 网络失败 / 非 2xx / 响应体不可解析 /
                响应中没有 web_search_tool_result 块。
        """
        api_key = _resolve_api_key()
        base_url, model = _search_options()
        endpoint = f"{base_url}/messages"

        body = {
            "model": model,
            "max_tokens": DEEPSEEK_SEARCH_MAX_TOKENS,
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": f"Perform a web search for the query: {query}",
                }],
            }],
            "tools": [{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": DEEPSEEK_SEARCH_MAX_USES,
            }],
        }
        headers = {
            "x-api-key": api_key,
            "authorization": f"Bearer {api_key}",
            "anthropic-version": DEEPSEEK_SEARCH_API_VERSION,
            "content-type": "application/json",
            "accept": "application/json",
            "user-agent": DEEPSEEK_SEARCH_USER_AGENT,
        }

        client = client or await self._get_client()
        try:
            resp = await client.post(endpoint, headers=headers, json=body)
        except httpx.TimeoutException:
            raise
        except Exception as e:
            raise WebSearchError(f"DeepSeek 搜索请求失败: {e}", "WEB_PROVIDER_ERROR") from e

        if not resp.is_success:
            raise WebSearchError(self._error_detail(resp), "WEB_PROVIDER_ERROR")

        try:
            data = resp.json()
        except Exception as e:
            raise WebSearchError(f"DeepSeek 返回了无法解析的响应体: {e}", "WEB_PROVIDER_ERROR") from e

        return self._parse(data)

    @staticmethod
    def _error_detail(resp: httpx.Response) -> str:
        """从非 2xx 响应中提取可读错误信息（对齐 DSH 的错误消息提取）。"""
        message = f"DeepSeek API 错误 (HTTP {resp.status_code})"
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                error = parsed.get("error")
                if isinstance(error, str) and error:
                    message = error
                elif isinstance(error, dict) and error.get("message"):
                    message = error["message"]
                elif parsed.get("message"):
                    message = str(parsed["message"])
        except Exception:
            pass
        return message

    @staticmethod
    def _parse(data: dict) -> SearchResult:
        """把 Messages 响应映射为规范化搜索结果（对齐 DSH mapAnthropicResponse）。

        - 遍历 web_search_tool_result 块中的 web_search_result 条目
        - 按 url 精确去重（首见生效；max_uses > 1 时同 URL 可跨多次搜索重复出现）
        - 摘要片段从 text 块 citations 按 url 拼接；page_age → published_at
        - 无 web_search_tool_result 块 → WebSearchError（对齐 DSH）
        """
        blocks = data.get("content") or []
        result_blocks = [
            b for b in blocks
            if isinstance(b, dict) and b.get("type") == "web_search_tool_result"
        ]
        if not result_blocks:
            raise WebSearchError(
                "DeepSeek 返回中没有 web_search_tool_result 块；本次请求可能未触发原生网络搜索",
                "WEB_PROVIDER_ERROR",
            )

        snippets = _citation_snippets(blocks)
        seen: set[str] = set()
        sources: list = []
        for block in result_blocks:
            for item in block.get("content") or []:
                if not isinstance(item, dict) or item.get("type") != "web_search_result":
                    continue
                url = (item.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)

                source: dict = {
                    "title": (item.get("title") or "").strip(),
                    "link": url,
                }
                snippet = snippets.get(url, "")
                if snippet:
                    source["snippet"] = snippet
                page_age = (item.get("page_age") or "").strip()
                if page_age:
                    source["published_at"] = page_age
                sources.append(source)

        # 赋值风格构造：_compat.dataclass 在低版本 Python 下不生成
        # mypy 可见的 __init__，避免 kwargs 构造报 call-arg。
        result = SearchResult()
        result.sources = sources
        return result


async def shutdown_clients() -> None:
    """关闭共享客户端与搜索 API 客户端，释放连接池资源（应用退出时调用）。"""
    await shutdown_client()
