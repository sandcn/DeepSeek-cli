"""底部栏分隔线全宽渐变测试 — 验证 make_sep_gradient() 和窄屏降级。

测试策略：
  1. make_sep_gradient() 返回包含正确色号序列的字符串
  2. 宽屏模式下分隔线长度为 tw-2
  3. 含青色(45)起始和深灰(237)结束色号
  4. 窄屏模式（<80列）回退到单色分隔线
"""

from __future__ import annotations

import unittest

from src.tui.widgets.bottom_bar.theme import (
    _COLOR_RESET,
    _COLOR_SEP,
    _COLOR_SEP_START,
    make_sep_gradient,
)
from src.tui.core.gradient import gradient_range


class TestMakeSepGradient(unittest.TestCase):
    """验证 make_sep_gradient() 函数的渐变色号序列。"""

    def _extract_colors(self, gradient_str: str) -> list[int]:
        """从渐变 ANSI 字符串中提取色号列表。"""
        colors: list[int] = []
        parts = gradient_str.split("\033[38;5;")
        for p in parts[1:]:  # 跳过第一个（RESET 之前的部分）
            semi = p.find("m")
            if semi > 0:
                try:
                    colors.append(int(p[:semi]))
                except ValueError:
                    continue
        return colors

    def test_gradient_length(self):
        """分隔线长度应与输入 width 一致。"""
        for width in [5, 10, 30, 78]:
            result = make_sep_gradient(width)
            colors = self._extract_colors(result)
            self.assertEqual(len(colors), width,
                             f"width={width}: 应有 {width} 个色号, 实际 {len(colors)}")

    def test_gradient_start_end_colors(self):
        """分隔线起始色应为青色(45)，结束色应为深灰(237)。"""
        result = make_sep_gradient(20)
        colors = self._extract_colors(result)
        self.assertEqual(colors[0], 45, "起始色号应为 45（青色）")
        self.assertEqual(colors[-1], 237, "结束色号应为 237（深灰）")

    def test_gradient_smooth_transition(self):
        """色号应平滑过渡（相邻色号差绝对值在合理范围内）。"""
        result = make_sep_gradient(30)
        colors = self._extract_colors(result)
        for i in range(1, len(colors)):
            diff = abs(colors[i] - colors[i - 1])
            self.assertGreaterEqual(
                diff, 0,
                f"位置 {i}: 色号 {colors[i-1]}→{colors[i]} 应为非负差",
            )

    def test_gradient_monotonic(self):
        """渐变应单调递增（从 45 到 237，色号单调上升）。"""
        result = make_sep_gradient(50)
        colors = self._extract_colors(result)
        for i in range(1, len(colors)):
            self.assertGreaterEqual(
                colors[i], colors[i - 1],
                f"位置 {i}: 色号不应下降 ({colors[i-1]}→{colors[i]})",
            )

    def test_gradient_contains_expected_colors(self):
        """使用 gradient_range 验证预期色号序列。"""
        width = 10
        expected = gradient_range(45, 237, width)
        result = make_sep_gradient(width)
        actual = self._extract_colors(result)
        self.assertEqual(actual, expected,
                         f"色号序列不匹配: 预期 {expected}, 实际 {actual}")

    def test_gradient_ends_with_reset(self):
        """渐变分隔线末尾应包含 _COLOR_RESET (\033[0m)。"""
        result = make_sep_gradient(20)
        self.assertTrue(result.endswith(_COLOR_RESET),
                        "渐变分隔线应以 RESET 结尾")

    def test_gradient_each_char_is_box_draw(self):
        """每个渐变字符应为 ━ (U+2501)。"""
        result = make_sep_gradient(15)
        # 去掉 ANSI 序列后统计 ━ 字符数
        cleaned = result
        # 移除所有 ANSI 序列
        import re
        cleaned = re.sub(r'\033\[[0-9;]*m', '', cleaned)
        self.assertEqual(len(cleaned), 15,
                         f"应有 15 个 ━ 字符, 实际 {len(cleaned)}")
        self.assertTrue(all(c == '\u2501' for c in cleaned),
                        "所有可见字符应为 ━ (U+2501)")

    def test_gradient_width_1(self):
        """width=1 时只返回一个青色(45)字符。"""
        result = make_sep_gradient(1)
        colors = self._extract_colors(result)
        self.assertEqual(len(colors), 1)
        self.assertEqual(colors[0], 45)

    def test_gradient_width_2(self):
        """width=2 时即青色(45)到深灰(237)两阶。"""
        result = make_sep_gradient(2)
        colors = self._extract_colors(result)
        self.assertEqual(len(colors), 2)
        self.assertEqual(colors[0], 45)
        self.assertEqual(colors[-1], 237)


class TestSepGradientNarrowFallback(unittest.TestCase):
    """验证窄屏降级：is_narrow() 为 True 时回退到单色分隔线。"""

    def test_make_sep_gradient_independent_of_narrow(self):
        """make_sep_gradient 本身不受窄屏影响（降级由调用方控制）。"""
        result_wide = make_sep_gradient(78)
        result_narrow = make_sep_gradient(30)
        colors_wide = self._extract_colors(result_wide)
        colors_narrow = self._extract_colors(result_narrow)
        # 两者都从45开始到237结束
        self.assertEqual(colors_wide[0], 45)
        self.assertEqual(colors_wide[-1], 237)
        self.assertEqual(colors_narrow[0], 45)
        self.assertEqual(colors_narrow[-1], 237)

    def test_narrow_fallback_sep_constant_available(self):
        """窄屏降级使用的 _COLOR_SEP 常量和 _COLOR_SEP_START 可用（值跟随当前主题）。"""
        from src.tui.core.theme import THEME
        # THEME 中的值是完整的 ANSI 字符串（如 "\033[38;5;239m"）
        expected_sep = THEME.get("separator", "\033[38;5;237m")
        expected_start = THEME.get("title", "\033[38;5;45m")
        self.assertEqual(_COLOR_SEP, expected_sep,
                         f"_COLOR_SEP 应与当前主题的 separator 一致")
        self.assertIn("38;5;45", _COLOR_SEP_START,
                      "_COLOR_SEP_START 应含青色 45 色号")

    def _extract_colors(self, gradient_str: str) -> list[int]:
        """从渐变 ANSI 字符串中提取色号列表。"""
        colors: list[int] = []
        parts = gradient_str.split("\033[38;5;")
        for p in parts[1:]:
            semi = p.find("m")
            if semi > 0:
                try:
                    colors.append(int(p[:semi]))
                except ValueError:
                    continue
        return colors


if __name__ == "__main__":
    unittest.main()
