"""TerminalWidthCache 单元测试。

测试范围：
  - 宽度/高度缓存命中与过期刷新
  - force_refresh 绕过 TTL
  - get_dimensions 便捷方法
  - 异常回退值
"""

from __future__ import annotations

import pytest
from unittest import mock


class TestTerminalWidthCache:
    """TerminalWidthCache 单元测试。"""

    @pytest.fixture
    def mock_terminal(self):
        """Mock _get_terminal_size 返回固定宽度 100、高度 40。"""
        with mock.patch(
            "src.tui._screen._get_terminal_size"
        ) as mock_term:
            mock_term.return_value = (100, 40)
            yield mock_term

    def test_get_width_returns_cached_value(self, mock_terminal):
        """get_width() 返回缓存宽度值。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        assert cache.get_width() == 100

    def test_get_height_returns_cached_value(self, mock_terminal):
        """get_height() 返回缓存高度值。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        assert cache.get_height() == 40

    def test_get_dimensions_returns_tuple(self, mock_terminal):
        """get_dimensions() 返回 (宽度, 高度) 元组。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        w, h = cache.get_dimensions()
        assert w == 100
        assert h == 40

    def test_cached_values_persist_within_ttl(self, mock_terminal):
        """TTL 内多次查询返回缓存值，不重新调用 fetcher。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        # 第一次调用填充缓存
        w1 = cache.get_width()
        h1 = cache.get_height()
        # 修改 mock 返回值但缓存仍在 TTL 内
        mock_terminal.return_value = (200, 80)
        w2 = cache.get_width()
        h2 = cache.get_height()
        # 应返回缓存值而非新值
        assert w2 == 100
        assert h2 == 40

    def test_force_refresh_bypasses_ttl(self, mock_terminal):
        """force_refresh() 绕过 TTL 立即刷新宽度和高度。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        # 填充缓存
        assert cache.get_width() == 100
        assert cache.get_height() == 40
        # 修改 mock 返回值
        mock_terminal.return_value = (120, 50)
        # force_refresh 绕过 TTL
        cache.force_refresh()
        assert cache.get_width() == 120
        assert cache.get_height() == 50

    def test_clear_resets_both_caches(self, mock_terminal):
        """clear() 清空宽度和高度缓存。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        # 填充缓存
        assert cache.get_width() == 100
        assert cache.get_height() == 40
        # 修改 mock 返回值
        mock_terminal.return_value = (200, 80)
        # 清空缓存
        cache.clear()
        # 下次查询应获取新值
        assert cache.get_width() == 200
        assert cache.get_height() == 80

    def test_height_fallback_on_exception(self):
        """get_terminal() 抛异常时高度回退 24。"""
        from src.tui._screen import TerminalWidthCache

        with mock.patch(
            "src.tui._screen._get_terminal_size",
            side_effect=Exception("not a tty"),
        ):
            cache = TerminalWidthCache(ttl=60.0)
            assert cache.get_height() == 24

    def test_width_fallback_on_exception(self):
        """_get_terminal_size() 抛异常时宽度回退 80。"""
        from src.tui._screen import TerminalWidthCache

        with mock.patch(
            "src.tui._screen._get_terminal_size",
            side_effect=Exception("not a tty"),
        ):
            cache = TerminalWidthCache(ttl=60.0)
            assert cache.get_width() == 80

    def test_get_default_returns_singleton(self, mock_terminal):
        """get_default() 返回全局单例。"""
        from src.tui._screen import TerminalWidthCache

        cache1 = TerminalWidthCache.get_default()
        cache2 = TerminalWidthCache.get_default()
        assert cache1 is cache2

    def test_refresh_height_bypasses_ttl(self, mock_terminal):
        """refresh_height() 强制刷新高度缓存。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        assert cache.get_height() == 40
        mock_terminal.return_value = (100, 50)
        assert cache.refresh_height() == 50
        assert cache.get_height() == 50

    def test_dimensions_independent_ttl(self, mock_terminal):
        """宽度和高度缓存的 TTL 独立管理。"""
        from src.tui._screen import TerminalWidthCache

        cache = TerminalWidthCache(ttl=60.0)
        # 填充两者
        assert cache.get_width() == 100
        assert cache.get_height() == 40
        # 仅刷新高度
        mock_terminal.return_value = (100, 50)
        cache.refresh_height()
        # 高度应更新，宽度应保持缓存
        assert cache.get_height() == 50
        assert cache.get_width() == 100
