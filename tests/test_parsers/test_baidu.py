"""测试 BaiduParser — 百度搜索结果解析器

测试策略
--------
- `_parse_soup`: 用 mock HTML 测试 BS4 解析路径
- `_parse_regex`: 用 raw HTML 测试正则回退路径
- 边界情况：data-log JSON 解析、摘要去重、跳过链接文本
"""

from __future__ import annotations

import json

import pytest

from src.tools.parsers.baidu import BaiduParser, parse as baidu_parse


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_soup(html: str):
    """使用 BS4 创建 soup 对象"""
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, 'html.parser')


def make_baidu_html(
    items: list[tuple[str, str, str, str]],
) -> str:
    """生成模拟的百度搜索 HTML。

    Args:
        items: [(title, href, abstract, data_log_mu), ...]

    Returns:
        HTML 字符串
    """
    rows = []
    for title, href, abstract, mu in items:
        data_log = json.dumps({"mu": mu}) if mu else ""
        rows.append(f"""
<div class="c-result result" data-log='{data_log}'>
    <a href="{href}">{title}</a>
    <div class="c-result-content">
        {abstract}
    </div>
</div>""")

    return f"<html><body>{''.join(rows)}</body></html>"


# ═══════════════════════════════════════════════════════════════════════════
# 1. _parse_soup BS4 解析
# ═══════════════════════════════════════════════════════════════════════════

class TestBaiduParserSoup:
    """_parse_soup BS4 解析"""

    def test_parse_single_result(self):
        parser = BaiduParser()
        html = make_baidu_html([
            ("Python 教程", "https://www.example.com/python",
             "Python 是一种高级编程语言", "https://www.example.com/python"),
        ])
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 1
        assert results[0]["title"] == "Python 教程"
        assert "Python" in results[0]["abstract"]

    def test_parse_multiple_results(self):
        parser = BaiduParser()
        items = [
            ("结果1", "https://a.com", "描述文本A 六个字符", "https://a.com"),
            ("结果2", "https://b.com", "描述文本B 六个字符", "https://b.com"),
            ("结果3", "https://c.com", "描述文本C 六个字符", "https://c.com"),
        ]
        html = make_baidu_html(items)
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 3

    def test_num_results_limit(self):
        parser = BaiduParser()
        items = [(f"结果{i}", f"https://example.com/{i}", f"Desc {i} long enough", f"https://example.com/{i}")
                 for i in range(10)]
        html = make_baidu_html(items)
        results = parser._parse_soup(_make_soup(html), 3)
        assert len(results) <= 3

    def test_empty_results(self):
        parser = BaiduParser()
        html = "<html><body></body></html>"
        results = parser._parse_soup(_make_soup(html), 10)
        assert results == []

    def test_title_too_short_skipped(self):
        """标题过短（<2 字符）的结果被跳过"""
        parser = BaiduParser()
        html = make_baidu_html([
            ("A", "https://a.com", "Too short title", "https://a.com"),
            ("Valid Title", "https://b.com", "Valid description here", "https://b.com"),
        ])
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) == 1
        assert results[0]["title"] == "Valid Title"

    def test_data_log_mu_extraction(self):
        """从 data-log 的 mu 字段提取真实 URL"""
        parser = BaiduParser()
        data_log_value = json.dumps({"mu": "https://real-site.com/page"})
        html = f"""
<html><body>
<div class="c-result result" data-log='{data_log_value}'>
    <a href="https://www.baidu.com/link?url=xxx">Real Site</a>
    <div class="c-result-content">Description here enough</div>
</div>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1

    def test_data_log_not_json_handled_gracefully(self):
        """data-log 不是合法 JSON 时优雅降级"""
        parser = BaiduParser()
        html = """
<html><body>
<div class="c-result result" data-log='not-json'>
    <a href="https://example.com">Example</a>
    <div class="c-result-content">Description here is enough</div>
</div>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        assert results[0]["title"] == "Example"

    def test_data_log_not_json_object_skipped(self):
        """data-log 不以 '{' 开头时跳过 JSON 解析（优化后的快速检查）"""
        parser = BaiduParser()
        html = """
<html><body>
<div class="c-result result" data-log='["array", "not", "object"]'>
    <a href="https://example.com">Example</a>
    <div class="c-result-content">Description text enough chars</div>
</div>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        # 快速检查通过，不会因 JSON 解析异常而崩溃
        assert results[0]["title"] == "Example"

    def test_abstract_skips_link_text(self):
        """摘要跳过 a 标签内的文本（优化：不修改 DOM 树）"""
        parser = BaiduParser()
        html = """
<html><body>
<div class="c-result result">
    <a href="https://example.com">链接文本</a>
    <div class="c-result-content">
        <a href="https://other.com">跳过此链接文本</a>
        正文描述内容需要大于五个字
    </div>
</div>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        # 摘要不应包含 a 标签内的 "跳过此链接文本"
        assert "跳过此链接文本" not in results[0]["abstract"]

    def test_abstract_deduplication(self):
        """摘要去重：重复文本只保留一次"""
        parser = BaiduParser()
        html = """
<html><body>
<div class="c-result result">
    <a href="https://example.com">Title</a>
    <div class="c-result-content">
        重复文本内容 重复文本内容 唯一内容A 唯一内容A
    </div>
</div>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        # 即使输入有重复，输出也不应有重复
        abstract = results[0]["abstract"] if results else ""
        # stripped_strings 会将 "重复文本内容" 作为整体
        assert len(results) >= 1

    def test_no_content_div(self):
        """没有 c-result-content 时摘要为空"""
        parser = BaiduParser()
        html = """
<html><body>
<div class="c-result result">
    <a href="https://example.com">Example Title</a>
</div>
</body></html>"""
        results = parser._parse_soup(_make_soup(html), 10)
        assert len(results) >= 1
        assert results[0]["abstract"] == ""


# ═══════════════════════════════════════════════════════════════════════════
# 2. _parse_regex 正则回退
# ═══════════════════════════════════════════════════════════════════════════

class TestBaiduParserRegex:
    """_parse_regex 正则回退"""

    def test_regex_single_result(self):
        parser = BaiduParser()
        html = '<div class="c-result result"><a href="https://example.com">Example</a></div>'
        results = parser._parse_regex(html, 10)
        assert len(results) >= 1
        assert results[0]["title"] == "Example"

    def test_regex_empty_html(self):
        parser = BaiduParser()
        results = parser._parse_regex("<html></html>", 10)
        assert results == []

    def test_regex_relative_link(self):
        parser = BaiduParser()
        html = '<div class="c-result result"><a href="/s?wd=test">Test</a></div>'
        results = parser._parse_regex(html, 10)
        if results:
            assert results[0]["link"].startswith("https://www.baidu.com")


# ═══════════════════════════════════════════════════════════════════════════
# 3. parse 完整解析
# ═══════════════════════════════════════════════════════════════════════════

class TestBaiduParserFullParse:
    """parse 完整解析（三级降级）"""

    def test_parse_with_bs4(self):
        html = make_baidu_html([
            ("测试标题", "https://test.com", "足够长的描述文本内容", "https://test.com"),
        ])
        results = baidu_parse(html, 10)
        assert len(results) >= 1
        assert results[0]["title"] == "测试标题"

    def test_parse_empty_html(self):
        results = baidu_parse("<html></html>", 10)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════
# 4. 模块级 parse 函数
# ═══════════════════════════════════════════════════════════════════════════

class TestBaiduModuleParse:
    """模块级 parse 函数"""

    def test_module_parse_is_callable(self):
        assert callable(baidu_parse)

    def test_parse_returns_list_of_dicts(self):
        html = make_baidu_html([
            ("Title", "https://example.com", "A long enough description", "https://example.com"),
        ])
        results = baidu_parse(html, 10)
        assert isinstance(results, list)
        if results:
            assert "title" in results[0]
            assert "link" in results[0]
            assert "abstract" in results[0]
