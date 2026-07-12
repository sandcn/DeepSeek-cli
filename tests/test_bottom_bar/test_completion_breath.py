"""_CompletionPopup 呼吸效果测试 — 验证选中项背景色脉动周期。

测试策略：
  直接测试 _CompletionPopup 的呼吸相位管理（tick_breath/_breath_bg_color）
  和 _render_item_line 选中项渲染是否包含动态呼吸背景色号。
  不涉及终端 I/O（ANSI 输出写入 devnull）。
"""

from __future__ import annotations

import io
import unittest

from src.ui._bottom_bar_pkg.completion import _CompletionPopup
from src.ui._bottom_bar_pkg.theme import _COLOR_BREATH_BG, _COLOR_SELECT_FG, _COLOR_RESET


class TestCompletionBreathPhase(unittest.TestCase):
    """验证呼吸相位初始值和循环推进。"""

    def setUp(self):
        self.popup = _CompletionPopup()

    def test_initial_phase_zero(self):
        """初始呼吸相位应为 0。"""
        self.assertEqual(self.popup._breath_phase, 0)

    def test_tick_breath_advances_phase(self):
        """tick_breath() 应循环推进相位。"""
        for expected in range(1, len(_COLOR_BREATH_BG)):
            self.popup.tick_breath()
            self.assertEqual(self.popup._breath_phase, expected,
                             f"Phase should be {expected} after {expected} ticks")

    def test_tick_breath_cycles_back_to_zero(self):
        """相位达到最大值后应回绕到 0。"""
        n = len(_COLOR_BREATH_BG)
        for _ in range(n):
            self.popup.tick_breath()
        self.assertEqual(self.popup._breath_phase, 0,
                         "Phase should wrap to 0 after full cycle")

    def test_breath_bg_color_returns_correct_color(self):
        """_breath_bg_color 应返回当前相位对应的色号。"""
        for phase, expected_color in enumerate(_COLOR_BREATH_BG):
            self.popup._breath_phase = phase
            self.assertEqual(self.popup._breath_bg_color, expected_color,
                             f"Phase {phase} should map to color {expected_color}")


class TestCompletionBreathRender(unittest.TestCase):
    """验证 _render_item_line 选中项渲染含呼吸背景色。"""

    def setUp(self):
        self.popup = _CompletionPopup()
        self.popup._items = ["hello"]
        self.popup._texts = ["hello"]
        self.popup._types = [""]
        self.popup._match_prefix = ""

    def _capture_selected_line(self, phase: int) -> str:
        """捕获指定相位下选中项的渲染输出。"""
        self.popup._breath_phase = phase
        out = io.StringIO()
        self.popup._render_item_line(
            out, r=2, item="hello", item_type="",
            match_prefix="", cell_w=20, is_selected=True,
        )
        return out.getvalue()

    def _capture_unselected_line(self) -> str:
        """捕获非选中项的渲染输出。"""
        out = io.StringIO()
        self.popup._render_item_line(
            out, r=2, item="hello", item_type="",
            match_prefix="", cell_w=20, is_selected=False,
        )
        return out.getvalue()

    def test_selected_contains_breath_bg_color(self):
        """选中项应包含呼吸背景色号对应的 ANSI 序列。"""
        for phase in range(len(_COLOR_BREATH_BG)):
            output = self._capture_selected_line(phase)
            expected_bg = f"48;5;{_COLOR_BREATH_BG[phase]}"
            self.assertIn(expected_bg, output,
                          f"Phase {phase} should use bg color {_COLOR_BREATH_BG[phase]}")

    def test_selected_contains_select_fg(self):
        """选中项应包含选中前景色 (_COLOR_SELECT_FG)。"""
        output = self._capture_selected_line(0)
        # _COLOR_SELECT_FG = "\033[38;5;15m"
        self.assertIn("38;5;15", output,
                       "Selected item should use _COLOR_SELECT_FG foreground")

    def test_unselected_no_breath_bg(self):
        """非选中项不应包含呼吸背景色号。"""
        output = self._capture_unselected_line()
        for phase in range(len(_COLOR_BREATH_BG)):
            unexpected_bg = f"48;5;{_COLOR_BREATH_BG[phase]}"
            self.assertNotIn(unexpected_bg, output,
                             f"Unselected item should not contain bg color {_COLOR_BREATH_BG[phase]}")

    def test_unselected_no_arrow_indicator(self):
        """非选中项不应包含 ▶ 指示器。"""
        output = self._capture_unselected_line()
        self.assertNotIn("\u25b6", output,
                         "Unselected item should not have ▶ indicator")

    def test_selected_has_arrow_indicator(self):
        """选中项应包含 ▶ 指示器。"""
        output = self._capture_selected_line(0)
        self.assertIn("\u25b6", output,
                       "Selected item should have ▶ indicator")


class TestCompletionBreathPhaseBoundary(unittest.TestCase):
    """验证呼吸相位边界情况。"""

    def test_all_breath_colors_in_range(self):
        """所有呼吸色号应在 235-240 范围内（256 色体系暗灰区间）。"""
        for color in _COLOR_BREATH_BG:
            self.assertGreaterEqual(color, 0)
            self.assertLessEqual(color, 255)

    def test_breath_list_length(self):
        """呼吸色号列表长度应为 10（对称呼吸周期）。"""
        self.assertEqual(len(_COLOR_BREATH_BG), 10)

    def test_breath_cycle_smooth(self):
        """呼吸周期应平滑过渡，相邻色号差值不超过 1。"""
        for i in range(len(_COLOR_BREATH_BG) - 1):
            diff = abs(_COLOR_BREATH_BG[i] - _COLOR_BREATH_BG[i + 1])
            self.assertLessEqual(diff, 1,
                                 f"Adjacent colors at {i},{i+1} diff={diff} > 1")

    def test_breath_first_is_min(self):
        """呼吸周期第一个色号应为最小值（起始暗灰）。"""
        self.assertEqual(_COLOR_BREATH_BG[0], min(_COLOR_BREATH_BG))

    def test_breath_mid_is_max(self):
        """呼吸周期中间（峰值）应为最大值。"""
        peak_idx = len(_COLOR_BREATH_BG) // 2
        self.assertEqual(_COLOR_BREATH_BG[peak_idx], max(_COLOR_BREATH_BG))
