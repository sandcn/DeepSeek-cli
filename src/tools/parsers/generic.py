"""Generic search results parser (fallback)"""

import re

from .base import BaseParser


class GenericParser(BaseParser):
    """Generic search results parser (fallback)."""

    def _parse_soup(self, soup, num_results):
        """通用解析兜底：提取所有 h3 > a 结构"""
        results = []
        for h3 in soup.find_all('h3')[:num_results]:
            a = h3.find('a')
            if not a:
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 2:
                continue
            href = a.get('href', '')
            results.append({'title': title, 'link': href, 'abstract': ''})
        return results

    def _parse_regex(self, html, num_results):
        """Parse using regex as fallback."""
        results = []
        pattern = r'<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        for href, title_html in re.findall(pattern, html, re.DOTALL)[:num_results]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if title and len(title) > 2:
                results.append({'title': title, 'link': href, 'abstract': ''})
        return results


parse = GenericParser().parse
