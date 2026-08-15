"""
web_search — 网页搜索工具

与内置 web_search 对齐的调用契约与内部实现：
  - 入参：仅 query（搜索关键词）
  - 内部实现（与内置 web_search 相同）：
      1. 检索 — 并发调用 search_providers 中的提供者获取来源列表
         （每条含 title / link / snippet），工具本身不解析任何搜索引擎 HTML
      2. 回答合成 — 用配置的 LLM 基于来源摘要合成可选回答（引用来源编号）
      3. 输出 — 可选答案 + 来源列表（每条为标题 markdown 链接、来源 URL
         与摘要片段）；模型应优先使用返回的摘要片段，并以 markdown 链接
         引用相关来源 URL
"""

from __future__ import annotations

import asyncio

import httpx

from .base import Func, tool_metadata
from .search_providers import (
    DuckDuckGoProvider,
    ScrapeProviders,
    SearchResult,
    get_shared_client,
    shutdown_client,
)
from ..core.constants import GREEN, YELLOW, DIM, RESET

# 合成回答的最大字符数（防御性截断，正常由提示词约束）
_MAX_ANSWER_CHARS = 600

# 单个来源摘要注入合成提示词的最大字符数
_MAX_SYNTH_SNIPPET_CHARS = 300


@tool_metadata(
    parallel_safe=True,
    requires_network=True,
    requires_terminal=False,
    timeout_estimate=30,
    category="general",
    priority=40,
    tool_category="read",
    description="网页搜索",
)
class WebSearchFunc(Func):
    name = "web_search"

    # 默认返回来源数上限（与内置 web_search 的来源列表规模一致）
    MAX_RESULTS = 10

    # 摘要片段最大字符数（超出截断，保持输出精炼）
    MAX_SNIPPET_CHARS = 200

    # 提供者聚合顺序：结构化 API 优先（提供可选回答），HTML 解析兜底
    PROVIDERS = (DuckDuckGoProvider, ScrapeProviders)

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索网页获取最新信息。返回可选的摘要回答和来源列表："
                    "每条包含标题、来源 URL 与摘要片段。"
                    "回答时优先使用返回的摘要片段，并以 markdown 链接引用相关来源 URL"
                    "（如 [标题](https://...)）。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "搜索关键词（中文/英文均可）。"
                                "建议包含关键实体、版本号或时间词以获取最新信息。"
                            ),
                        }
                    },
                    "required": ["query"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        query = arguments.get("query", "")
        if len(query) > max_len:
            query = query[: max_len - 3] + "..."
        return query

    def __init__(self, query):
        super().__init__()
        self.query = query

    async def execute(self) -> str:
        if not self.query or not self.query.strip():
            return "(搜索失败: 参数 query 不能为空)"
        try:
            return await self._search_async()
        except httpx.TimeoutException:
            return f"(搜索超时: {self.query})"
        except Exception as e:
            return f"(搜索失败: {e})"

    async def _search_async(self) -> str:
        """检索 → 回答合成 → 输出（与内置 web_search 的实现流程一致）"""
        client = await get_shared_client()
        provider_results: list[SearchResult] = await asyncio.gather(
            *[provider().search(self.query, client=client) for provider in self.PROVIDERS]
        )

        merged: list[dict] = []
        for r in provider_results:
            merged.extend(r.sources)

        sources = self._dedupe(merged, self.MAX_RESULTS)
        if not sources:
            return f"(搜索未找到结果: {self.query})"

        # 回答合成：LLM 基于来源摘要生成（失败/中断则降级为提供者回答或纯来源列表）
        answer = await _synthesize_answer(self.query, sources)
        if not answer:
            answer = next((r.answer for r in provider_results if r.answer), None)

        return self._format_result(self.query, answer, sources)

    @staticmethod
    def _normalize_url(url: str) -> str:
        """规范化 URL 用于去重：小写、去 scheme/www、去尾斜杠与 fragment、忽略 query"""
        if not url:
            return ""
        url = url.strip()
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/")
        normalized = f"{host}{path}"
        return normalized or url.lower()

    @classmethod
    def _dedupe(cls, results: list[dict], max_results: int | None = None) -> list[dict]:
        """按规范化 URL 去重（无链接时按标题去重），保持顺序并裁剪数量"""
        seen_urls: set[str] = set()
        seen_titles: set[str] = set()
        out: list[dict] = []
        for r in results:
            title = (r.get("title") or "").strip()
            link = (r.get("link") or "").strip()
            if not title:
                continue

            key = cls._normalize_url(link)
            if key:
                if key in seen_urls:
                    continue
                seen_urls.add(key)
            else:
                title_key = title.lower()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

            out.append(r)
            if max_results is not None and len(out) >= max_results:
                break
        return out

    @classmethod
    def _format_result(cls, query: str, answer: str | None, sources: list[dict]) -> str:
        """格式化为：可选答案 + 来源列表（标题为 markdown 链接，附带摘要片段）"""
        lines: list[str] = []
        if answer:
            lines.append(f"答案: {answer}")
            lines.append("")

        lines.append(f"来源 ({len(sources)} 条):")
        for i, s in enumerate(sources, 1):
            title = (s.get("title") or "").strip()
            link = (s.get("link") or "").strip()
            snippet = (s.get("snippet") or "").strip()

            lines.append("")
            if link:
                lines.append(f"{i}. [{title}]({link})")
            else:
                lines.append(f"{i}. {title}")
            if snippet:
                if len(snippet) > cls.MAX_SNIPPET_CHARS:
                    snippet = snippet[: cls.MAX_SNIPPET_CHARS] + "..."
                lines.append(f"   {snippet}")
        return "\n".join(lines)

    @classmethod
    async def shutdown(cls):
        """关闭共享 AsyncClient，释放连接池资源（应用退出时调用）"""
        await shutdown_client()

    close_client = shutdown

    # ── 显示 ──

    async def display(self) -> str:
        Func._publish_tool_text(f"\n  {GREEN}🔍 网页搜索: {self.query}{RESET}")
        result = await self.execute()

        if result.startswith("("):
            Func._publish_tool_text(f"  {YELLOW}{result}{RESET}")
        else:
            Func._publish_tool_text(f"  {DIM}{result}{RESET}")

        return result


# ═══════════════════════════════════════════════════════════
#  回答合成（与内置 web_search 的回答生成一致）
# ═══════════════════════════════════════════════════════════

def _build_synthesis_messages(query: str, sources: list[dict]) -> list:
    """构建回答合成提示词：问题 + 带编号来源（标题/URL/摘要）。

    编号与最终输出的来源列表一致，模型回答中以 [n] 引用。
    """
    source_lines: list[str] = []
    for i, s in enumerate(sources, 1):
        title = (s.get("title") or "").strip()
        link = (s.get("link") or "").strip()
        snippet = (s.get("snippet") or "").strip()

        lines = [f"[{i}] {title}"]
        if link:
            lines.append(f"URL: {link}")
        if snippet:
            if len(snippet) > _MAX_SYNTH_SNIPPET_CHARS:
                snippet = snippet[: _MAX_SYNTH_SNIPPET_CHARS] + "..."
            lines.append(f"摘要: {snippet}")
        source_lines.append("\n".join(lines))

    system = (
        "你是网页搜索助手。根据给定的搜索结果回答用户问题。要求：\n"
        "- 只用来源摘要中的信息回答，不要编造\n"
        "- 简洁准确，用与问题相同的语言回答（不超过150字）\n"
        "- 引用来源时用方括号编号，如 [1][2]\n"
        "- 搜索结果信息不足时直接说明，不要臆测"
    )
    user = f"问题: {query}\n\n搜索结果:\n" + "\n\n".join(source_lines)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


async def _synthesize_answer(query: str, sources: list[dict]) -> str | None:
    """用配置的 LLM 基于来源摘要合成回答。

    任何失败（未配置 API Key / 调用失败 / 被中断 / 空回答）都返回 None，
    输出降级为纯来源列表 — 与内置 web_search 的“可选回答”语义一致。
    """
    if not sources:
        return None
    try:
        from ..config import API_KEY
        if not API_KEY or not API_KEY.strip():
            return None
        from ..api.model_async import call_model_sync_async
    except Exception:
        return None

    try:
        _, content, _, _ = await call_model_sync_async(
            _build_synthesis_messages(query, sources),
            override_max_retries=1,
            fixed_delay_sec=0,
        )
    except Exception:
        return None

    content = (content or "").strip()
    if not content or content == "(已中断)":
        return None
    if len(content) > _MAX_ANSWER_CHARS:
        content = content[: _MAX_ANSWER_CHARS] + "..."
    return content
