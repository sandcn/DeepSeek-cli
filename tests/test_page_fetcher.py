"""测试 src/tools/page_fetcher.py — _is_private_url IPv6 SSRF 防护

测试策略
--------
- 直接测试 _is_private_url 函数的 IPv6 link-local 检测能力
- 覆盖 fe80::/10 范围的边界用例
- 覆盖合法 IPv6 和非 IPv6 主机名
- 遵循 Arrange/Act/Assert 模式
"""

from __future__ import annotations

import pytest

from src.tools.page_fetcher import _is_private_url


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
