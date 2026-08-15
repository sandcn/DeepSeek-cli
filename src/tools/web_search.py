"""
web_search — 网页搜索工具（DeepSeek 官方原生搜索）

对齐 DSH @deepseek-ai/dsh-tool-web + dsh-web-search-deepseek 的实现：
  - 入参：仅 query（搜索关键词）
  - 内部实现（与 DSH web_search 相同）：
      1. 检索 — 通过 DeepSeekSearchProvider 调用 DeepSeek 的 Anthropic
         兼容 Messages API，由原生服务端工具 web_search_20250305 完成
         真实联网搜索；工具本身不解析任何搜索引擎 HTML
      2. 裁剪 — 来源数上限 MAX_RESULTS（对齐 DSH WEB_SEARCH_MAX_RESULTS=8），
         超限截断并置 truncated 标记
      3. 输出 — 来源列表（标题 markdown 链接 + 摘要片段 + 发布日期）与
         引用指令；可选回答由调用方模型基于来源自行组织（DSH 的
         DeepSeek 提供者不生成回答）
"""

from __future__ import annotations

import httpx

from .base import Func, tool_metadata
from .search_providers import (
    DeepSeekSearchProvider,
    WebSearchError,
    shutdown_clients,
)
from ..core.constants import GREEN, YELLOW, DIM, RESET


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

    # 默认返回来源数上限（对齐 DSH WEB_SEARCH_MAX_RESULTS）
    MAX_RESULTS = 8

    # 摘要片段最大字符数（超出截断，保持输出精炼）
    MAX_SNIPPET_CHARS = 200

    # 搜索提供者（对齐 DSH：工具只依赖接口，提供者可替换）
    PROVIDER = DeepSeekSearchProvider

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": (
                    "搜索网页获取当前信息。返回来源列表：每条包含标题、来源 URL 与"
                    "摘要片段。回答时优先使用返回的摘要片段，并以 markdown 链接引用"
                    "相关来源 URL；需要某个来源的完整内容时再用 web_fetch。"
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
        query = (self.query or "").strip()
        if not query:
            return "(搜索失败: 参数 query 不能为空)"
        try:
            result = await self.PROVIDER().search(query)
        except httpx.TimeoutException:
            return f"(搜索超时: {query})"
        except WebSearchError as e:
            return f"(搜索失败: [{e.code}] {e})"
        except Exception as e:
            return f"(搜索失败: {e})"

        sources, truncated = self._cap_sources(result.sources, self.MAX_RESULTS)
        return self._format_result(query, result.answer, sources, truncated)

    @staticmethod
    def _cap_sources(sources: list, max_results: int) -> "tuple[list, bool]":
        """执行 maxResults 裁剪（对齐 DSH WebRuntime.capSources）。

        提供者超量返回时截断 sources 并置 truncated 标记。
        """
        if len(sources) <= max_results:
            return sources, False
        return sources[:max_results], True

    @staticmethod
    def _source_label(source: dict) -> str:
        """来源显示标签：标题，缺失时用主机名（对齐 DSH sourceLabel）。"""
        title = (source.get("title") or "").strip()
        if title:
            return title
        link = (source.get("link") or "").strip()
        try:
            from urllib.parse import urlparse

            return urlparse(link).netloc or link
        except Exception:
            return link

    @classmethod
    def _format_result(
        cls, query: str, answer: "str | None", sources: list, truncated: bool = False
    ) -> str:
        """格式化输出（对齐 DSH formatSearchOutput）：

        可选答案 + 来源列表（`- [标签](url) — 摘要 (日期)`）；
        无来源时输出“未找到结果。”；截断时附提示；末尾附引用指令。
        """
        parts: list[str] = []
        if answer:
            parts.append(f"答案: {answer}")

        if sources:
            lines = [f"来源 ({len(sources)} 条):"]
            for s in sources:
                label = cls._source_label(s)
                link = (s.get("link") or "").strip()

                meta: list[str] = []
                snippet = (s.get("snippet") or "").strip()
                if snippet:
                    if len(snippet) > cls.MAX_SNIPPET_CHARS:
                        snippet = snippet[: cls.MAX_SNIPPET_CHARS] + "..."
                    meta.append(snippet)
                published = (s.get("published_at") or "").strip()
                if published:
                    meta.append(f"({published})")

                suffix = f" — {' '.join(meta)}" if meta else ""
                lines.append(f"- [{label}]({link}){suffix}")
            parts.append("\n".join(lines))
        else:
            parts.append("未找到结果。")

        if truncated:
            parts.append(f"(仅显示前 {len(sources)} 条来源，可细化查询获取更多。)")

        parts.append(
            "回答时请优先使用上述来源的摘要片段，并以 markdown 链接引用相关来源 URL。"
        )
        return "\n\n".join(parts)

    @classmethod
    async def shutdown(cls):
        """关闭共享客户端与搜索 API 客户端，释放连接池资源（应用退出时调用）"""
        await shutdown_clients()

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
