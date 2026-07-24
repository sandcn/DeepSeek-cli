"""GitHub search results parser (repository search)"""

import re

from .base import BaseParser

_GITHUB_REPO_HREF_RE = re.compile(r'^/[\w.-]+/[\w.-]+$')


class GitHubParser(BaseParser):
    """GitHub search results parser (repository search).

    Parses GitHub repository search results from
    https://github.com/search?q={query}&type=repositories
    """

    RESULT_SELECTORS = [
        'div[data-testid="results-list"] > div',
        'div.repo-list-item',
        'div[class*="Box"] div[class*="Box"]',
    ]

    def _parse_soup(self, soup, num_results):
        """Parse GitHub repository search results using BeautifulSoup"""
        results = []
        containers = []

        # 多重选择器降级
        for sel in self.RESULT_SELECTORS:
            candidates = soup.select(sel)
            if candidates:
                containers = candidates
                break

        # 如果已有容器，遍历提取
        if containers:
            for container in containers[:num_results]:
                item = self._extract_item(container)
                if item:
                    results.append(item)
            if results:
                return results

        # fallback: 提取所有 h3 > a 仓库链接
        for h3 in soup.find_all('h3')[:num_results]:
            a = h3.find('a')
            if not a:
                continue
            href = a.get('href', '')
            if not href.startswith('/') or href.count('/') < 2:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            # 查找同级或父级中的描述
            abstract = ''
            for p in h3.find_all_next('p', limit=3):
                text = p.get_text(strip=True)
                if len(text) > 10:
                    abstract = text
                    break
            results.append({
                'title': title,
                'link': 'https://github.com' + href,
                'abstract': abstract,
            })

        return results

    def _extract_item(self, container):
        """从单个搜索结果容器中提取仓库信息"""
        # 提取仓库链接
        a_tag = container.find('a', href=_GITHUB_REPO_HREF_RE)
        if not a_tag:
            a_tag = container.select_one('h3 a')
        if not a_tag:
            a_tag = container.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        title = a_tag.get_text(strip=True)
        if not title or not href:
            return None

        # 补全链接
        if href.startswith('/'):
            link = 'https://github.com' + href
        elif href.startswith('http'):
            link = href
        else:
            link = 'https://github.com/' + href

        # 提取描述（p 标签）
        abstract = ''
        for p in container.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 5:
                abstract = text
                break

        # 如果没有 p 描述，尝试从其他常见元素提取
        if not abstract:
            # 合并为单个 CSS 选择器，一次 select_one 替代多次
            elem = container.select_one(
                '[class*="description"], [class*="summary"], [class*="color-fg-muted"]'
            )
            if elem:
                abstract = elem.get_text(strip=True)

        return {
            'title': title,
            'link': link,
            'abstract': abstract,
        }

    def _parse_regex(self, html, num_results):
        """Parse GitHub search results using regex (fallback)"""
        results = []
        seen_links = set()

        # 匹配仓库链接模式: /owner/repo
        pattern = (
            r'<a[^>]*href="(/\w[\w.-]*/\w[\w.-]*)"[^>]*>'  # 仓库链接
            r'\s*([^<]+)\s*'                                 # 仓库名
        )
        for href, title_html in re.findall(pattern, html)[:num_results * 2]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if not title or len(title) < 2:
                continue
            link = 'https://github.com' + href
            if link in seen_links:
                continue
            seen_links.add(link)

            # 尝试提取紧跟的描述文本
            abstract = ''
            desc_pattern = (
                re.escape(href) + r'[^<]*</a>\s*'  # 链接后面
                r'(?:<[^>]+>\s*)*'                 # 可能的标签
                r'([^<]{10,}?)'                    # 描述文本（至少10字符）
                r'(?:<|$)'                          # 到下一个标签或结尾
            )
            desc_match = re.search(desc_pattern, html)
            if desc_match:
                abstract = desc_match.group(1).strip()
                abstract = re.sub(r'\s+', ' ', abstract)[:200]

            results.append({
                'title': title,
                'link': link,
                'abstract': abstract,
            })

            if len(results) >= num_results:
                break

        return results


parse = GitHubParser().parse
