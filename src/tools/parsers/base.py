"""Base parser for search results — provides shared parsing orchestration"""

from abc import ABC, abstractmethod
import logging

_logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """Base class for search result parsers.

    Subclasses must implement:
        _parse_soup(self, soup, num_results) -> list[dict]
        _parse_regex(self, html, num_results) -> list[dict]
    """

    @abstractmethod
    def _parse_soup(self, soup, num_results):
        """Parse search results from a BeautifulSoup object.

        Args:
            soup: A BeautifulSoup parsed HTML object.
            num_results: Maximum number of results to return.

        Returns:
            List of dicts with keys: title, link, abstract.
        """
        ...

    @abstractmethod
    def _parse_regex(self, html, num_results):
        """Parse search results using regex as fallback.

        Args:
            html: Raw HTML string.
            num_results: Maximum number of results to return.

        Returns:
            List of dicts with keys: title, link, abstract.
        """
        ...

    def parse(self, html, num_results):
        """Parse search results.

        Multi-tier fallback strategy:
            1. BeautifulSoup + lxml   (fastest parser)
            2. BeautifulSoup + html.parser (built-in, no extra dependency)
            3. Regex fallback         (always works)

        Args:
            html: Raw HTML content from search page.
            num_results: Maximum number of results to return.

        Returns:
            List of dicts with keys: title, link, abstract.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return self._parse_regex(html, num_results)

        # 一级降级：lxml（最快解析器）
        try:
            soup = BeautifulSoup(html, 'lxml')
            return self._parse_soup(soup, num_results)
        except Exception:
            _logger.debug("BeautifulSoup(lxml) 解析失败，降级到 html.parser")

        # 二级降级：html.parser（内置，无需额外依赖）
        try:
            soup = BeautifulSoup(html, 'html.parser')
            return self._parse_soup(soup, num_results)
        except Exception:
            _logger.debug("BeautifulSoup(html.parser) 解析失败，降级到正则")

        # 三级降级：正则回退
        return self._parse_regex(html, num_results)

    @staticmethod
    def _extract_text(element):
        """Extract clean text from a BeautifulSoup element."""
        if element is None:
            return ''
        return element.get_text(strip=True)

    @staticmethod
    def _clean_url(url, base_url=None):
        """Clean and normalize a URL.

        If base_url is provided, joins relative paths.
        """
        if not url:
            return ''
        from urllib.parse import urlparse, urljoin
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return url
        if base_url and url.startswith('/'):
            return urljoin(base_url, url)
        return url





# ═══════════════════════════════════════════════════════════
# 共享 URL 提取工具函数（消除 baidu/bing 中的重复实现）
# ═══════════════════════════════════════════════════════════

def extract_real_url(url: str, search_domain: str = "") -> str:
    """从搜索引擎重定向 URL 中提取真实目标 URL。

    通用实现，支持多种搜索引擎的重定向格式。
    会检查 query string 中的常见 URL 参数（如 q, url, continue, extra 等）。

    Args:
        url: 搜索引擎返回的 URL（可能是重定向链接）
        search_domain: 搜索引擎域名片段（如 "baidu.com", "bing.com"），
                       用于判断是否需要提取

    Returns:
        真实的目标 URL
    """
    if not url:
        return ""

    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(url)

    # 如果 URL 域名不含搜索引擎域名，说明不是重定向链接，直接返回
    if search_domain and parsed.netloc and search_domain not in parsed.netloc:
        return url

    qs = parse_qs(parsed.query)

    # 尝试从 extra 参数中提取（百度特有格式）
    if "extra" in qs:
        try:
            import json
            extra_str = qs["extra"][0]
            extra_data = json.loads(extra_str)
            loc = extra_data.get("loc", "") or extra_data.get("log_loc", "")
            if loc:
                loc = unquote(unquote(loc))
                if loc.startswith("http"):
                    return loc
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # 尝试常见的 URL 参数
    for key in ("q", "url", "continue", "pu", "word", "wd"):
        if key in qs:
            val = qs[key][0]
            if val and val.startswith("http"):
                return val

    return url
