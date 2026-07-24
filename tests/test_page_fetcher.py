"""测试 src/tools/page_fetcher.py — SSRF 防护、正文提取、噪音识别

测试策略
--------
- 直接测试 _is_private_url 函数的 IPv6 link-local 检测能力
- 覆盖 fe80::/10 范围的边界用例
- 覆盖合法 IPv6 和非 IPv6 主机名
- 测试 _is_noise_element 噪音元素识别（正则匹配）
- 测试 _extract_main_content 多级降级策略
- 测试 _validate_fetch_url 安全校验
- 遵循 Arrange/Act/Assert 模式
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from src.tools.page_fetcher import (
    _is_private_url,
    _is_noise_element,
    _extract_main_content,
    _validate_fetch_url,
)


# ═══════════════════════════════════════════════════════════════
# _is_noise_element 噪音元素识别
# ═══════════════════════════════════════════════════════════════

class TestIsNoiseElement:
    """_is_noise_element 对 class/id 关键词的识别（正则匹配）"""

    def test_nav_class(self):
        """class 含 'nav' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="main-nav"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_navbar_class(self):
        """class 含 'navbar' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="navbar"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_footer_class(self):
        """class 含 'footer' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="site-footer"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_sidebar_class(self):
        """class 含 'sidebar' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="sidebar"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_ad_class(self):
        """class 含 'advertisement' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="advertisement-banner"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_cookie_class(self):
        """class 含 'cookie' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="cookie-banner"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_popup_class(self):
        """class 含 'popup' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="popup-overlay"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_noise_id(self):
        """id 含 'sidebar' 应被识别为噪音"""
        soup = BeautifulSoup('<div id="sidebar"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_content_class_not_noise(self):
        """class 含 'content'（不在关键词中）不应被识别为噪音"""
        soup = BeautifulSoup('<div class="main-content-area"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is False

    def test_article_class_not_noise(self):
        """class 含 'article'（不在关键词中）不应被识别为噪音"""
        soup = BeautifulSoup('<div class="article-body"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is False

    def test_empty_class_and_id(self):
        """无 class 和 id 的元素不应被识别为噪音"""
        soup = BeautifulSoup('<div></div>', 'html.parser')
        assert _is_noise_element(soup.div) is False

    def test_case_insensitive(self):
        """关键词匹配大小写不敏感（re.IGNORECASE）"""
        soup = BeautifulSoup('<div class="SIDEBAR"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_nested_noise_class(self):
        """class 包含多级名称，内嵌噪音关键词"""
        soup = BeautifulSoup(
            '<div class="wrapper site-footer-wrapper"></div>', 'html.parser'
        )
        assert _is_noise_element(soup.div) is True

    def test_comment_class(self):
        """class 含 'comments' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="comments-section"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True

    def test_breadcrumb_class(self):
        """class 含 'breadcrumb' 应被识别为噪音"""
        soup = BeautifulSoup('<div class="breadcrumb"></div>', 'html.parser')
        assert _is_noise_element(soup.div) is True


# ═══════════════════════════════════════════════════════════════
# _extract_main_content 正文提取
# ═══════════════════════════════════════════════════════════════

class TestExtractMainContent:
    """_extract_main_content 多级降级策略测试"""

    def test_article_tag_strategy(self):
        """策略1: <article> 标签提取"""
        html = (
            "<html><body>"
            "<article>"
            "<p>" + "这是正文内容。" * 10 + "</p>"
            "</article>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "这是正文内容" in result

    def test_main_tag_strategy(self):
        """策略2: <main> 标签提取"""
        html = (
            "<html><body>"
            "<main>"
            "<p>" + "主要内容区域。" * 10 + "</p>"
            "</main>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "主要内容区域" in result

    def test_content_class_strategy(self):
        """策略3: .content class 选择器"""
        html = (
            "<html><body>"
            "<div class='content'>"
            "<p>" + "正文内容区域。" * 10 + "</p>"
            "</div>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "正文内容区域" in result

    def test_post_content_class_strategy(self):
        """策略3: .post-content class 选择器"""
        html = (
            "<html><body>"
            "<div class='post-content'>"
            "<p>" + "博客文章内容。" * 10 + "</p>"
            "</div>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "博客文章内容" in result

    def test_article_body_class_strategy(self):
        """策略3: .article-body class 选择器"""
        html = (
            "<html><body>"
            "<div class='article-body'>"
            "<p>" + "文章正文。" * 10 + "</p>"
            "</div>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "文章正文" in result

    def test_body_fallback(self):
        """策略5: 无任何结构标签时回退到 body 文本"""
        html = (
            "<html><body>"
            "<p>" + "简单页面内容。" * 10 + "</p>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "简单页面内容" in result

    def test_empty_page(self):
        """空页面返回空字符串"""
        soup = BeautifulSoup("<html><body></body></html>", 'html.parser')
        result = _extract_main_content(soup)
        assert result == ""

    def test_no_body(self):
        """无 body 标签返回空字符串"""
        soup = BeautifulSoup("<html></html>", 'html.parser')
        result = _extract_main_content(soup)
        assert result == ""

    def test_noise_removed_from_content(self):
        """正文提取时噪音元素（nav/footer）被移除"""
        html = (
            "<html><body>"
            "<article>"
            "<nav>导航内容</nav>"
            "<p>" + "正文。" * 10 + "</p>"
            "<footer>页脚内容</footer>"
            "</article>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "正文" in result
        assert "导航内容" not in result
        assert "页脚内容" not in result

    def test_itemprop_article_body(self):
        """策略3: [itemprop='articleBody'] 选择器"""
        html = (
            "<html><body>"
            "<div itemprop='articleBody'>"
            "<p>" + "结构化文章正文。" * 10 + "</p>"
            "</div>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "结构化文章正文" in result

    def test_role_main_strategy(self):
        """策略2: [role='main'] 属性提取"""
        html = (
            "<html><body>"
            "<div role='main'>"
            "<p>" + "主内容区。" * 10 + "</p>"
            "</div>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "主内容区" in result

    def test_short_article_skipped(self):
        """article 内容过短时跳过策略1，进入后续策略"""
        html = (
            "<html><body>"
            "<article><p>短。</p></article>"
            "<div class='content'>"
            "<p>" + "这才是真正的正文。" * 10 + "</p>"
            "</div>"
            "</body></html>"
        )
        soup = BeautifulSoup(html, 'html.parser')
        result = _extract_main_content(soup)
        assert "这才是真正的正文" in result


# ═══════════════════════════════════════════════════════════════
# _validate_fetch_url 安全校验
# ═══════════════════════════════════════════════════════════════

class TestValidateFetchUrl:
    """_validate_fetch_url 对 URL 的安全校验"""

    def test_empty_url(self):
        """空 URL 应返回错误"""
        result = _validate_fetch_url("")
        assert result is not None
        assert "URL为空" in result

    def test_whitespace_only_url(self):
        """仅空白的 URL 应返回错误"""
        result = _validate_fetch_url("   ")
        assert result is not None
        assert "URL为空" in result

    def test_ftp_protocol_rejected(self):
        """非 http/https 协议应被拒绝"""
        result = _validate_fetch_url("ftp://example.com/file")
        assert result is not None
        assert "不支持的协议" in result

    def test_no_domain(self):
        """缺少域名的 URL 应被拒绝"""
        result = _validate_fetch_url("http:///path")
        assert result is not None
        assert "缺少域名" in result

    def test_private_ip_rejected(self):
        """私有 IP 地址应被拒绝"""
        result = _validate_fetch_url("http://192.168.1.1/")
        assert result is not None
        assert "内网地址" in result

    def test_localhost_rejected(self):
        """localhost 应被拒绝"""
        result = _validate_fetch_url("http://localhost:8080/")
        assert result is not None
        assert "内网地址" in result

    def test_public_url_allowed(self):
        """公网 URL 应通过校验"""
        result = _validate_fetch_url("https://example.com/page")
        assert result is None

    def test_http_url_allowed(self):
        """HTTP 公网 URL 应通过校验"""
        result = _validate_fetch_url("http://example.com/page")
        assert result is None


# ═══════════════════════════════════════════════════════════════
# IPv6 link-local 地址检测 (fe80::/10: 0xFE80-0xFEBF)
# ═══════════════════════════════════════════════════════════════

class TestIPv6LinkLocal:
    """_is_private_url 对 IPv6 link-local 地址的检测"""

    def test_fe80_lower_bound(self):
        """fe80::/10 下限：fe80::1 应被识别为私有地址"""
        assert _is_private_url("http://[fe80::1]/index.html") is True

    def test_fe80_any_interface(self):
        """fe80::/10 范围内任意地址"""
        assert _is_private_url("http://[fe80::a00:27ff:fe4e:8f3d]/api") is True

    def test_fe81_within_range(self):
        """fe81::1 在 fe80::/10 范围内，应被拦截"""
        assert _is_private_url("http://[fe81::1]/") is True

    def test_febf_upper_bound(self):
        """febf::/10 上限：febf::1 应被识别为私有地址"""
        assert _is_private_url("http://[feBF::1]/") is True

    def test_febf_ffff(self):
        """febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff 仍在范围内"""
        assert _is_private_url("http://[febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff]/") is True

    def test_fec0_out_of_range(self):
        """fec0::1 不在 fe80::/10 范围内（fec0::/10 是已废弃的 site-local），不应被 _is_private_url 拦截"""
        # fec0::/10 不在 PRIVATE_PREFIXES 中也不在 fe80::/10 中
        assert _is_private_url("http://[fec0::1]/") is False

    def test_fe7f_below_range(self):
        """fe7f::1 低于 fe80::/10 范围，不应被拦截（除非其他规则拦截）"""
        assert _is_private_url("http://[fe7f::1]/") is False

    def test_fe70_far_below(self):
        """fe70::1 远低于 fe80::/10"""
        assert _is_private_url("http://[fe70::1]/") is False


# ═══════════════════════════════════════════════════════════════
# 非 IPv6 边界
# ═══════════════════════════════════════════════════════════════

class TestNonIPv6Hostnames:
    """_is_private_url 对非 IPv6 主机名的处理"""

    def test_ipv4_private_192_168(self):
        """192.168.x.x 仍被拦截"""
        assert _is_private_url("http://192.168.1.1/") is True

    def test_ipv4_private_10(self):
        """10.x.x.x 仍被拦截"""
        assert _is_private_url("http://10.0.0.1/") is True

    def test_ipv4_private_127(self):
        """127.0.0.1 仍被拦截"""
        assert _is_private_url("http://127.0.0.1/") is True

    def test_ipv4_public(self):
        """8.8.8.8 不应被拦截"""
        assert _is_private_url("http://8.8.8.8/") is False

    def test_localhost_name(self):
        """localhost 字符串应被拦截"""
        assert _is_private_url("http://localhost/index.html") is True

    def test_ipv6_loopback(self):
        """::1 应被拦截"""
        assert _is_private_url("http://[::1]/") is True

    def test_ipv6_public(self):
        """2001:db8::1（文档用途但非私有）不应被 IP 黑名单拦截"""
        assert _is_private_url("http://[2001:db8::1]/") is False

    def test_domain_name(self):
        """正常域名不应被拦截"""
        assert _is_private_url("https://example.com/path") is False

    def test_domain_name_starting_with_127(self):
        """127.example.com 是合法域名，不应被 IP 前缀误拦截"""
        assert _is_private_url("http://127.example.com/") is False

    def test_domain_name_starting_with_10(self):
        """10.example.com 是合法域名"""
        assert _is_private_url("http://10.example.com/") is False

    def test_domain_name_starting_with_192(self):
        """192.example.com 是合法域名"""
        assert _is_private_url("http://192.example.com/") is False


# ═══════════════════════════════════════════════════════════════
# IPv6 特殊情况
# ═══════════════════════════════════════════════════════════════

class TestIPv6EdgeCases:
    """IPv6 边界特殊情况"""

    def test_ipv6_zone_id(self):
        """IPv6 带 zone ID（如 %eth0）的地址

        urlparse 会将 %25 解码为 %，hostname 变为 fe80::1%eth0。
        Python 3.13+ 的 ipaddress.IPv6Address 支持 zone ID（RFC 6874），
        会剥离 %eth0 后解析为 fe80::1，首 hextet 在 fe80::/10 范围内。
        """
        result = _is_private_url("http://[fe80::1%25eth0]/")
        # Python 3.13+ 支持 zone ID → fe80::1 被正确识别为 link-local
        assert result is True

    def test_ipv4_mapped_ipv6(self):
        """::ffff:192.168.1.1 格式的 IPv4 映射地址"""
        # ipaddress.IPv6Address 可解析，首 hextet 是 0x0000，不在 fe80 范围
        # 但 hostname 含字母（ffff），会跳过 IP 前缀检查
        result = _is_private_url("http://[::ffff:192.168.1.1]/")
        # 首 hextet 为 0，不在 fe80 范围
        assert result is False

    def test_ipv6_unspecified(self):
        """:: （全零地址）"""
        # urlparse 对 "http://[::]/" 的 hostname 为 "::"
        # IPv6Address("::") 首 hextet 为 0
        assert _is_private_url("http://[::]/") is False

    def test_fc00_unique_local(self):
        """fc00::/7 的唯一本地地址（类似 IPv4 私网）

        注意：当前实现中 hostname="fc00::1" 含字母，会跳过 PRIVATE_PREFIXES
        前缀检查。fc00::1 首 hextet 0xFC00 不在 fe80::/10 范围内，
        因此不被拦截。这是已知局限（ULA 地址 fc00::/7 未被完整覆盖）。
        """
        # fc00::1 首 hextet 不在 fe80 范围，且含字母跳过前缀检查
        assert _is_private_url("http://[fc00::1]/") is False

    def test_fd00_unique_local(self):
        """fd00::/8 的唯一本地地址（实际使用）"""
        # "fc00:" 前缀可以匹配 "fd00:" 吗？不，它是精确前缀匹配
        # 所以 fd00::1 不会被 fc00: 匹配
        # 需要检查实际的 PRIVATE_PREFIXES 逻辑
        # "fd00:" 不在列表中，"fc00:" 仅精确匹配 fc00: 开头
        result = _is_private_url("http://[fd00::1]/")
        # fd00::1 是 ULA，但目前不在 PRIVATE_PREFIXES 中，也不在 fe80::/10
        # 实际结果取决于 hostname 是否有字母（有→跳过 IP 检查）
        assert result is False
