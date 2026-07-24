"""测试 BingParser — Bing 搜索结果解析器

测试策略
--------
- `_parse_soup`: 用 mock HTML 测试 BS4 解析路径
- `_parse_regex`: 用 raw HTML 测试正则回退路径
- 验证模块级常量 _BING_RESULT_SELECTORS / _BING_ABSTRACT_SELECTORS 优化
"""

from __future__ import annotations

import pytest

from src.tools.parsers.bing import BingParser, parse as bing_parse
from src.tools.parsers.bing import _BING_RESULT_SELECTORS, _BING_ABSTRACT_SELECTORS


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_soup(html: str):
    """使用 BS4 创建 soup 对象"""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'html.parser')


def make_bing_html(
    items: list[tuple[str, str, str]],
    use_h2: bool = False,
) -> str:
    """生成模拟的 Bing 搜索 HTML。

    Args:
        items: [(title, href, abstract), ...]
        use_h2: True 使用 h2 包裹（对应 .b_algo h2 选择器）

    Returns:
        HTML 字符串
    """
    rows = []
    for title, href, abstract in items:
        if use_h2:
            rows.append(f"""
<h2 class="b_algo">
    <a href="{href}">{title}</a>
</h2>
<div class="b_caption"><p>{abstract}</p></div>""")
        else:
            rows.append(f"""
<li class="b_algo">
    <h2><a href="{href}">{title}</a></h2>
    <div class="b_caption"><p>{abstract}</p></div>
</li>""")

    return f"<html><body>{''.join(rows)}</body></html>"


# ═══════════════════════════════════════════════════════════════════════════
# 1. 模块级常量验证
# ═══════════════════════════════════════════════════════════════════════════

class TestBingModuleConstants:
    """验证模块级常量优化"""

    def test_result_selectors_are_module_level(self):
        """RESULT_SELECTORS 已提升为模块级常量"""
        assert isinstance(_BING_RESULT_SELECTORS, list)
        assert '.b_algo' in _BING_RESULT_SELECTORS
        assert '.b_algo h2' in _BING_RESULT_SELECTORS

    def test_abstract_selectors_are_module_level(self):
        """ABSTRACT_SELECTORS 已提升为模块级常量"""
        assert isinstance(_BING_ABSTRACT_SELECTORS, list)
        assert '.b_caption p' in _BING_ABSTRACT_SELECTORS
        assert '.b_caption' in _BING_ABSTRACT_SELECTORS
        assert '.b_lineclamp2' in _BING_ABSTRACT_SELECTORS

    def test_selectors_not_class_attributes(self):
        """类属性 RESULT_SELECTORS / ABSTRACT_SELECTORS 已移除"""
        parser = BingParser()
        assert not hasattr(parser, 'RESULT_SELECTORS')
        assert not hasattr(parser, 'ABSTRACT_SELECTORS')


# ═══════════════════════════════════════════════════════════════════════════
# 2. _parse_soup BS4 解析
# ═══════════════════════════════════════════════════════════════════════════

class TestBingParserSoup:
    """_parse_soup BS4 解析"""

    def test_parse_single_result(self):
        parser = BingParser()
        html = make_bing_html([
            ("Bing Search Result", "https://www.example.com", "This is a description"),
        ])
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 1
        assert results[0]["title"] == "Bing Search Result"
        assert results[0]["link"] == "https://www.example.com"
        assert "description" in results[0]["abstract"]

    def test_parse_multiple_results(self):
        parser = BingParser()
        items = [
            ("Result 1", "https://a.com", "Description A"),
            ("Result 2", "https://b.com", "Description B"),
            ("Result 3", "https://c.com", "Description C"),
        ]
        html = make_bing_html(items)
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 3

    def test_num_results_limit(self):
        parser = BingParser()
        items = [(f"Result {i}", f"https://example.com/{i}", f"Desc {i}")
                 for i in range(10)]
        html = make_bing_html(items)
        results = parser._parse_soup(_make_soup(html), 3)
        assert len(results) <= 3

    def test_empty_results(self):
        parser = BingParser()
        html = "<html><body></body></html>"
        results = parser._parse_soup(_make_soup(html), 10)
        assert results == []

    def test_h2_wrapper_format(self):
        """测试 .b_algo h2 选择器（h2 直接包裹 a 标签）"""
        parser = BingParser()
        html = make_bing_html([
            ("H2 Result", "https://h2-example.com", "H2 description"),
        ], use_h2=True)
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        assert results[0]["title"] == "H2 Result"

    def test_b_lineclamp2_abstract(self):
        """测试 .b_lineclamp2 作为摘要选择器"""
        parser = BingParser()
        html = """
<html><body>
<li class="b_algo">
    <h2><a href="https://example.com">Title</a></h2>
    <div class="b_lineclamp2">Line clamped description text</div>
</li>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        assert "Line clamped" in results[0]["abstract"]

    def test_fallback_p_tag_abstract(self):
        """当所有选择器都失败时，回退到通用 p 标签提取摘要"""
        parser = BingParser()
        html = """
<html><body>
<li class="b_algo">
    <h2><a href="https://example.com">Title</a></h2>
    <p>Generic paragraph description</p>
</li>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        assert "Generic paragraph" in results[0]["abstract"]

    def test_no_title_skipped(self):
        """没有标题的结果被跳过"""
        parser = BingParser()
        html = """
<html><body>
<li class="b_algo">
    <a href="https://example.com"></a>
    <p>Description</p>
</li>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. _parse_regex 正则回退
# ═══════════════════════════════════════════════════════════════════════════

class TestBingParserRegex:
    """_parse_regex 正则回退"""

    def test_regex_single_result(self):
        parser = BingParser()
        html = '<li class="b_algo"><a href="https://example.com">Example</a></li>'
        results = parser._parse_regex(html, 10)
        assert len(results) >= 1
        assert results[0]["title"] == "Example"

    def test_regex_empty_html(self):
        parser = BingParser()
        results = parser._parse_regex("<html></html>", 10)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. parse 完整解析
# ═══════════════════════════════════════════════════════════════════════════

class TestBingParserFullParse:
    """parse 完整解析"""

    def test_parse_with_bs4(self):
        html = make_bing_html([
            ("Bing Title", "https://example.com", "Bing description"),
        ])
        results = bing_parse(html, 10)
        assert len(results) >= 1
        assert results[0]["title"] == "Bing Title"

    def test_parse_empty_html(self):
        results = bing_parse("<html></html>", 10)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. 模块级 parse 函数
# ═══════════════════════════════════════════════════════════════════════════

class TestBingModuleParse:
    """模块级 parse 函数"""

    def test_module_parse_is_callable(self):
        assert callable(bing_parse)

    def test_parse_returns_list_of_dicts(self):
        html = make_bing_html([
            ("Title", "https://example.com", "Description"),
        ])
        results = bing_parse(html, 10)
        assert isinstance(results, list)
        if results:
            assert "title" in results[0]
            assert "link" in results[0]
            assert "abstract" in results[0]
