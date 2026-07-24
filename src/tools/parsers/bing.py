"""Bing search results parser"""

import re

from .base import BaseParser


_BING_RESULT_SELECTORS = ['.b_algo', '.b_algo h2']
_BING_ABSTRACT_SELECTORS = ['.b_caption p', '.b_caption', '.b_lineclamp2']


class BingParser(BaseParser):
    """Bing search results parser."""

    def _parse_soup(self, soup, num_results):
        """Parse Bing search results using BeautifulSoup"""
        results = []
        # 必应 PC 结果容器
        selectors = _BING_RESULT_SELECTORS
        for sel in selectors:
            containers = soup.select(sel)
            if containers:
                break

        for container in containers[:num_results]:
            item = {}

            if container.name == 'h2':
                a = container.find('a')
            else:
                a = container.find('h2')
                if a:
                    a = a.find('a')
                else:
                    a = container.find('a')

            if not a:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            item['title'] = title

            href = a.get('href', '')
            item['link'] = href

            # 摘要
            abstract = ''
            for sel in _BING_ABSTRACT_SELECTORS:
                p = container.select_one(sel)
                if p:
                    abstract = p.get_text(strip=True)
                    if abstract:
                        break
            if not abstract:
                p = container.select_one('p')
                if p:
                    abstract = p.get_text(strip=True)
            item['abstract'] = abstract

            results.append(item)
        return results

    def _parse_regex(self, html, num_results):
        """Parse Bing search results using regex (fallback)"""
        results = []
        # 简单匹配 li.b_algo 结构
        pattern = r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        for href, title_html in re.findall(pattern, html, re.DOTALL)[:num_results]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if title:
                results.append({
                    'title': title,
                    'link': href,
                    'abstract': ''
                })
        return results


parse = BingParser().parse
