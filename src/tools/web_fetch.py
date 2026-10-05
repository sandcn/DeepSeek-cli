"""
web_fetch — 网页全文获取工具

原 web_search 的 mode='fetch' 拆分为独立工具：
给定 URL，抓取并提取标题/发布时间/正文（去导航/广告/页脚噪音）。
SSRF 防护（禁止内网地址、仅 http/https）与正文提取复用 page_fetcher，
连接池复用 search_providers 的共享 AsyncClient。
"""

from __future__ import annotations

import httpx

from .base import Func, tool_metadata
from .fetch_providers import HttpPageFetcher
from .search_providers import get_shared_client
from ..core.constants import GREEN, YELLOW, DIM, RESET


@tool_metadata(
    parallel_safe=True,
    requires_network=True,
    requires_terminal=False,
    timeout_estimate=15,
    category="general",
    priority=41,
    tool_category="read",
    description="网页全文获取",
)
class WebFetchFunc(Func):
    name = "web_fetch"

    # 默认抓取提供者（无内核/无清单条目时的回退）
    PROVIDER = HttpPageFetcher

    @classmethod
    def _resolve_provider(cls):
        """解析抓取提供者（内核 ``ctx.web_fetch`` 服务优先，回退默认注册表）。"""
        from .fetch_provider_registry import active_fetch_provider

        return active_fetch_provider() or cls.PROVIDER()

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "web_fetch",
                "description": (
                    "获取指定 URL 的网页全文内容（自动提取标题、发布时间和正文，"
                    "去除导航/广告/页脚噪音）。查找资源先用 web_search，"
                    "需要页面完整内容时再用本工具。"
                    "仅 http/https，禁止内网地址。"
                    "获取的代码片段须标注来源 URL 和验证状态。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "要获取全文的网页 URL（仅 http/https，禁止内网地址）",
                        }
                    },
                    "required": ["url"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        url = arguments.get("url", "")
        if len(url) > max_len:
            url = url[: max_len - 3] + "..."
        return url

    def __init__(self, url):
        super().__init__()
        self.url = url

    async def execute(self) -> str:
        url = (self.url or "").strip()
        if not url:
            return "(获取网页失败: 参数 url 不能为空)"
        provider = self._resolve_provider()
        try:
            # 复用 search_providers 的共享连接池，避免重复建连
            client = await get_shared_client()
            result = await provider.fetch(url, client=client)
        except httpx.TimeoutException:
            return f"(获取网页超时: {url})"
        except Exception as e:
            return f"(获取网页失败: {e})"

        if "error" in result:
            return result["error"]

        return provider.format(result)

    async def display(self) -> str:
        Func._publish_tool_text(f"\n  {GREEN}📄 获取网页: {self.url}{RESET}")
        result = await self.execute()

        if result.startswith("("):
            Func._publish_tool_text(f"  {YELLOW}{result}{RESET}")
        else:
            Func._publish_tool_text(f"  {DIM}{result}{RESET}")

        return result
