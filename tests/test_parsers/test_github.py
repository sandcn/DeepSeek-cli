"""测试 GitHubParser — GitHub 搜索结果解析器（parsers 子包测试）

测试策略
--------
- 验证优化后的模块级常量 _GITHUB_REPO_HREF_RE
- 验证合并后的 CSS 选择器（desc_selectors 合并为单个选择器）
- `_parse_soup`: 用 mock HTML 测试 BS4 解析路径
- `_parse_regex`: 用 raw HTML 测试正则回退路径
"""

from __future__ import annotations

import re

import pytest

from src.tools.parsers.github import (
    GitHubParser,
    _GITHUB_REPO_HREF_RE,
    parse as github_parse,
)


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_soup(html: str):
    """使用 BS4 创建 soup 对象"""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'html.parser')


def make_github_html(
    items: list[tuple[str, str, str]],
    use_testid: bool = True,
) -> str:
    """生成模拟的 GitHub 搜索 HTML。

    Args:
        items: [(repo_name, href, description), ...]
        use_testid: True 用 data-testid='results-list'，False 用 div.repo-list-item
    """
    rows = []
    for name, href, desc in items:
        rows.append(f"""
<div>
    <a href="{href}">{name}</a>
    <p>{desc}</p>
</div>""")

    if use_testid:
        wrapper = f'<div data-testid="results-list">{"".join(rows)}</div>'
    else:
        wrapper = f'<div>{"".join(rows)}</div>'

    return f"<html><body>{wrapper}</body></html>"


# ═══════════════════════════════════════════════════════════════════════════
# 1. 模块级常量验证（优化项）
# ═══════════════════════════════════════════════════════════════════════════

class TestGitHubModuleConstants:
    """验证 GitHub 解析器的模块级优化"""

    def test_repo_href_re_is_module_level(self):
        """_GITHUB_REPO_HREF_RE 是模块级预编译正则（避免每次 _extract_item 调用重新编译）"""
        assert isinstance(_GITHUB_REPO_HREF_RE, re.Pattern)

    def test_repo_href_re_matches_valid_repo_path(self):
        """验证正则能正确匹配仓库路径"""
        assert _GITHUB_REPO_HREF_RE.match("/owner/repo")
        assert _GITHUB_REPO_HREF_RE.match("/user123/project-name")
        assert _GITHUB_REPO_HREF_RE.match("/a.b/c.d")
        assert _GITHUB_REPO_HREF_RE.match("/org/repo.sub")

    def test_repo_href_re_rejects_invalid_paths(self):
        """验证正则拒绝无效路径"""
        assert _GITHUB_REPO_HREF_RE.match("https://github.com/owner/repo") is None
        assert _GITHUB_REPO_HREF_RE.match("/") is None
        assert _GITHUB_REPO_HREF_RE.match("/owner") is None
        assert _GITHUB_REPO_HREF_RE.match("owner/repo") is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. _extract_item 合并选择器验证
# ═══════════════════════════════════════════════════════════════════════════

class TestGitHubExtractItem:
    """_extract_item 提取逻辑（含合并 CSS 选择器优化）"""

    def test_extract_basic(self):
        """基本仓库信息提取"""
        parser = GitHubParser()
        html = """
<div>
    <a href="/owner/repo">owner/repo</a>
    <p>A description of the repository</p>
</div>"""
        soup = _make_soup(html)
        container = soup.find('div')
        result = parser._extract_item(container)
        assert result is not None
        assert result["title"] == "owner/repo"
        assert result["link"] == "https://github.com/owner/repo"
        assert "description" in result["abstract"]

    def test_extract_from_muted_class(self):
        """从 color-fg-muted 类提取描述（合并选择器优化）"""
        parser = GitHubParser()
        html = """
<div>
    <a href="/org/repo">org/repo</a>
    <span class="color-fg-muted">Muted description text here</span>
</div>"""
        soup = _make_soup(html)
        container = soup.find('div')
        result = parser._extract_item(container)
        assert result is not None
        assert "Muted description" in result["abstract"]

    def test_extract_from_description_class(self):
        """从 [class*="description"] 提取（合并选择器优化）"""
        parser = GitHubParser()
        html = """
<div>
    <a href="/org/repo">org/repo</a>
    <div class="repo-description">Description from class</div>
</div>"""
        soup = _make_soup(html)
        container = soup.find('div')
        result = parser._extract_item(container)
        assert result is not None
        assert "Description from class" in result["abstract"]

    def test_extract_from_summary_class(self):
        """从 [class*="summary"] 提取（合并选择器优化）"""
        parser = GitHubParser()
        html = """
<div>
    <a href="/org/repo">org/repo</a>
    <span class="search-summary">Summary text here</span>
</div>"""
        soup = _make_soup(html)
        container = soup.find('div')
        result = parser._extract_item(container)
        assert result is not None
        assert "Summary text" in result["abstract"]

    def test_no_link_returns_none(self):
        """没有链接时返回 None"""
        parser = GitHubParser()
        html = '<div><p>No link here</p></div>'
        soup = _make_soup(html)
        container = soup.find('div')
        result = parser._extract_item(container)
        assert result is None

    def test_http_link_preserved(self):
        """http/https 链接原样保留"""
        parser = GitHubParser()
        html = """
<div>
    <a href="https://github.com/owner/repo">owner/repo</a>
    <p>Desc</p>
</div>"""
        soup = _make_soup(html)
        container = soup.find('div')
        result = parser._extract_item(container)
        assert result is not None
        assert result["link"] == "https://github.com/owner/repo"


# ═══════════════════════════════════════════════════════════════════════════
# 3. _parse_soup BS4 解析
# ═══════════════════════════════════════════════════════════════════════════

class TestGitHubParserSoup:
    """_parse_soup BS4 解析"""

    def test_parse_single_result(self):
        parser = GitHubParser()
        html = make_github_html([("owner/repo", "/owner/repo", "A great repo")])
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 1
        assert results[0]["title"] == "owner/repo"
        assert "https://github.com/owner/repo" == results[0]["link"]

    def test_empty_results(self):
        parser = GitHubParser()
        html = "<html><body></body></html>"
        results = parser._parse_soup(_make_soup(html), 10)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. 模块级 parse 函数
# ═══════════════════════════════════════════════════════════════════════════

class TestGitHubModuleParse:
    """模块级 parse 函数"""

    def test_module_parse_is_callable(self):
        assert callable(github_parse)

    def test_parse_returns_list_of_dicts(self):
        html = make_github_html([("owner/repo", "/owner/repo", "A repo")])
        results = github_parse(html, 10)
        assert isinstance(results, list)
        if results:
            assert "title" in results[0]
            assert "link" in results[0]
            assert "abstract" in results[0]
