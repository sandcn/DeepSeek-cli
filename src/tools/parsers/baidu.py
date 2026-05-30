"""Baidu search results parser"""

import json
import re

from .base import BaseParser, extract_real_url


class BaiduParser(BaseParser):
    """Baidu search results parser."""

    def _parse_soup(self, soup, num_results):
        """Parse Baidu search results using BeautifulSoup"""
        results = []
        result_divs = soup.select('.c-result.result')

        for div in result_divs[:num_results]:
            item = {}

            # 从 data-log 中提取 mu
            real_url = ''
            data_log_str = div.get('data-log', '')
            if data_log_str:
                try:
                    log_data = json.loads(data_log_str)
                    real_url = log_data.get('mu', '')
                except (json.JSONDecodeError, TypeError):
                    pass

            title_tag = div.find('a')
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not title or len(title) < 2:
                continue
            item['title'] = title

            href = title_tag.get('href', '') or real_url
            if not href:
                href = div.get('data-url', '') or div.get('url', '')
            if href and not href.startswith('http'):
                href = 'https://www.baidu.com' + href
            item['link'] = extract_real_url(href, "baidu.com") if href else ''

            # 摘要
            content_div = div.select_one('.c-result-content')
            if content_div:
                for a_tag in content_div.find_all('a'):
                    a_tag.extract()
                texts = []
                for t in content_div.stripped_strings:
                    t = t.strip()
                    if len(t) > 5:
                        texts.append(t)
                seen_t = set()
                unique_texts = []
                for t in texts:
                    if t not in seen_t:
                        seen_t.add(t)
                        unique_texts.append(t)
                item['abstract'] = ' '.join(unique_texts[:5]) if unique_texts else ''
            else:
                item['abstract'] = ''

            results.append(item)
        return results

    def _parse_regex(self, html, num_results):
        """Parse Baidu search results using regex (fallback)"""
        results = []
        pattern = (
            r'<div[^>]*class="[^"]*c-result[^"]*"[^>]*>.*?'
            r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        )
        for href, title_html in re.findall(pattern, html, re.DOTALL):
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if title and len(title) > 2:
                link = href
                if link and not link.startswith('http'):
                    link = 'https://www.baidu.com' + link
                results.append({
                    'title': title,
                    'link': extract_real_url(link, "baidu.com") if link else '',
                    'abstract': ''
                })
        return results[:num_results]


parse = BaiduParser().parse
