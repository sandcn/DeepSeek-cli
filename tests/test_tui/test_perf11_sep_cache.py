"""回归测试 — PERF-11 分隔线缓存生效（sep_style 对象稳定性）。

覆盖：
  - BUG（PERF-11 声称的缓存实际失效）：sep_style(active=True) 每次返回新
    Style 对象 → status_bar 的 ``sep`` use_memo deps ``(width, sep_style)``
    引用比较永远 miss（``_object_is`` 对 Style 仅 ``is`` 比较）→ 每帧重建
    分隔线 Line。修复后同 0.1s 时间桶内返回**同一 Style 实例** → use_memo
    引用命中 → 分隔线 Line 跨帧复用（零重建）。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.app import _theme
from src.tui.ink import hooks
from src.tui.ink.fiber import Fiber, TAG_FUNCTION
from src.tui.ink.output import Line


class TestSepStyleObjectStability:
    """sep_style 对象稳定性（PERF-11 落地核心）。"""

    def test_active_same_bucket_same_object(self):
        """活跃期同一 0.1s 桶内返回同一 Style 实例（引用比较可命中）。"""
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            s1 = _theme.sep_style(True)
            s2 = _theme.sep_style(True)
        assert s1 is s2, (
            "同桶 sep_style(True) 应返回同一 Style 实例（修复前每帧新对象）"
        )

    def test_inactive_returns_constant(self):
        """空闲期返回模块级 _S_SEP 常量（恒同对象）。"""
        assert _theme.sep_style(False) is _theme._S_SEP

    def test_cross_bucket_returns_new_object(self):
        """跨时间桶返回新 Style 实例（呼吸色更新需重建 Line）。"""
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            s1 = _theme.sep_style(True)
        with patch("src.tui.app._theme.time.monotonic", return_value=100.15):
            s2 = _theme.sep_style(True)
        assert s1 is not s2, "跨桶 sep_style(True) 应返回新 Style 实例（呼吸更新）"


class TestStatusBarSepCacheHit:
    """status_bar 分隔线 use_memo 跨帧复用（PERF-11 修复验证）。"""

    def _render_sep(self, fiber: Fiber, width: int):
        fiber.reset_hooks()
        s = _theme.sep_style(True)
        return hooks.use_memo(
            lambda: Line.of("\u2501" * max(1, width), s),
            (width, s),
        )

    def test_sep_line_reused_within_bucket(self):
        """同桶内连续两帧：分隔线 Line 复用（修复前每帧重建）。"""
        fiber = Fiber(TAG_FUNCTION, type="StatusBar")
        hooks._current_fiber_stack = [fiber]
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            sep1 = self._render_sep(fiber, 80)
            sep2 = self._render_sep(fiber, 80)
        assert sep1 is sep2, "同桶内分隔线 Line 应跨帧复用（引用命中）"

    def test_sep_line_rebuilt_across_bucket(self):
        """跨桶（呼吸色更新）分隔线 Line 重建。"""
        fiber = Fiber(TAG_FUNCTION, type="StatusBar")
        hooks._current_fiber_stack = [fiber]
        with patch("src.tui.app._theme.time.monotonic", return_value=100.0):
            sep1 = self._render_sep(fiber, 80)
        with patch("src.tui.app._theme.time.monotonic", return_value=100.15):
            sep2 = self._render_sep(fiber, 80)
        assert sep1 is not sep2, "跨桶呼吸色更新应重建分隔线 Line"
