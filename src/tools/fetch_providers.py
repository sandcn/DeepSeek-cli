"""Web 抓取提供者 — 默认 HTTP 正文抓取实现。

「一切皆插件」：``web_fetch`` 工具不再直接调用 ``page_fetcher.fetch_page``，
而是经可替换的抓取提供者（fetch provider）获取页面；默认 ``http`` 提供者
包装 ``src.tools.page_fetcher`` 的 httpx + BeautifulSoup 正文提取实现。

提供者只需实现两个方法：

    async def fetch(self, url: str, client=None) -> dict   # 页面结构（含 error）
    def format(self, data: dict) -> str                     # 结构 → 可读文本

外部插件可注册自定义提供者（如无头浏览器渲染、站点专用提取器），与内置走
同一套解析/替换/撤销机制（对应 dsh 的 ``dsh-tool-web`` 拆包）。
"""

from __future__ import annotations

from typing import Any, Optional


class HttpPageFetcher:
    """默认抓取提供者 — httpx 请求 + 启发式正文提取。"""

    label = "HTTP"

    async def fetch(self, url: str, client: Optional[Any] = None) -> dict:
        from .page_fetcher import fetch_page

        return await fetch_page(url, client=client)

    def format(self, data: dict) -> str:
        from .page_fetcher import format_fetch_result

        return format_fetch_result(data)


__all__ = ["HttpPageFetcher"]
