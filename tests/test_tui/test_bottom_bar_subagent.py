"""_BottomBar subagent 面板渲染测试"""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import MagicMock, patch

from src.tui.widgets.bottom_bar import _BottomBar


def _mock_terminal(width=80, height=30):
    """创建模拟 Blessed Terminal 对象，返回指定的 width/height。"""
    mock_term = MagicMock()
    mock_term.width = width
    mock_term.height = height
    # move_xy 返回实际的 ANSI 序列
    mock_term.move_xy.side_effect = lambda x, y: f"\033[{y+1};{x+1}H"
    mock_term.clear_eol = "\033[K"
    return mock_term


class TestBottomBarSubagentPanel(unittest.TestCase):
    """验证 _BottomBar subagent 面板功能的正确性。

    核心场景：
      1. set_subagent_frame 影响 _bottom_lines 计数
      2. 清除 subagent 帧后 _bottom_lines 恢复
      3. force_redraw() 输出包含 subagent 面板行内容
      4. subagent 面板行在分隔线与状态行之间
      5. 终端高度不足时降级（全屏清除，不崩溃）
      6. 补全弹窗与 subagent 面板共存
    """

    def setUp(self):
        self.bb = _BottomBar()
        # 激活底部栏（模拟 setup 后的状态）
        self.bb._active = True
        # 空输入文本
        self.bb._last_text = ""
        self.bb._last_status = ""
        self.bb._input_cursor_pos = 0
        self.bb._last_cursor_pos = 0
        # 未处于流式状态
        self.bb._status_active = False
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    # ── 场景 1：set_subagent_frame 影响 _bottom_lines ──────

    def test_set_subagent_frame_increases_bottom_lines(self):
        """设置 5 行 subagent 面板后 _bottom_lines 应增加 5。"""
        initial = self.bb._bottom_lines
        # 空输入时 _compute_input_rows() = _MIN_INPUT_ROWS = 3
        # _bottom_lines = 2 + 0 + 3 = 5
        self.assertEqual(initial, 5,
                         "初始 _bottom_lines 应为 5（2 分隔线+状态行 + 3 最小输入行）")

        self.bb.set_subagent_frame(["line1", "line2", "line3", "line4", "line5"])
        new_total = self.bb._bottom_lines
        expected = 2 + 5 + self.bb._compute_input_rows()
        self.assertEqual(new_total, expected,
                         f"_bottom_lines 应为 {expected}（2 + 5 + _compute_input_rows()）")
        self.assertEqual(new_total - initial, 5,
                         "_bottom_lines 应增加恰好 5 行")

    # ── 场景 2：清除 subagent 帧恢复 _bottom_lines ────────

    def test_clear_subagent_frame_restores_bottom_lines(self):
        """设置 subagent 面板后清空，_bottom_lines 应恢复原值。"""
        initial = self.bb._bottom_lines

        # 先设置 5 行
        self.bb.set_subagent_frame(["line1", "line2", "line3", "line4", "line5"])
        with_panel = self.bb._bottom_lines
        self.assertGreater(with_panel, initial,
                           "设置 subagent 面板后 _bottom_lines 应增加")

        # 再设置空列表清除
        self.bb.set_subagent_frame([])
        restored = self.bb._bottom_lines
        expected = 2 + self.bb._compute_input_rows()
        self.assertEqual(restored, expected,
                         f"清除后 _bottom_lines 应恢复为 {expected}")
        self.assertEqual(restored, initial,
                         "清除后 _bottom_lines 应与初始值相同")

    # ── 场景 3：force_redraw 输出包含 subagent 面板行 ────

    def test_force_redraw_includes_subagent_lines(self):
        """设置 subagent 面板行后 force_redraw() 输出应包含面板行内容。"""
        mock_term = _mock_terminal(width=80, height=30)

        self.bb.set_subagent_frame(["line1", "line2", "line3"])

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term), \
             patch.object(self.bb, '_format_status', return_value="STATUS"):
            self.bb.force_redraw()

        output = out.getvalue()
        self.assertIn("line1", output, "ANSI 输出应包含 subagent 第 1 行")
        self.assertIn("line2", output, "ANSI 输出应包含 subagent 第 2 行")
        self.assertIn("line3", output, "ANSI 输出应包含 subagent 第 3 行")

    # ── 场景 4：subagent 面板在分隔线与状态行之间 ────────

    def test_subagent_panel_between_separator_and_status(self):
        """subagent 面板行应在分隔线（━）之后、状态行之前。"""
        mock_term = _mock_terminal(width=80, height=30)

        self.bb.set_subagent_frame(["line1", "line2", "line3"])

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term), \
             patch.object(self.bb, '_format_status', return_value="STATUS"):
            self.bb.force_redraw()

        output = out.getvalue()

        # 分隔线（━）应在 subagent 行之前
        sep_pos = output.index("\u2501")
        line1_pos = output.index("line1")
        line3_pos = output.index("line3")
        status_pos = output.index("STATUS")

        self.assertLess(sep_pos, line1_pos,
                        "分隔线（━）应在 subagent 第 1 行之前")
        self.assertLess(line3_pos, status_pos,
                        "subagent 最后一行应在状态行之前")

    # ── 场景 5：终端高度不足时降级（全屏清除，不崩溃） ──

    def test_terminal_too_small_degradation(self):
        """终端高度不足以容纳底部栏 + subagent 面板时不应崩溃。

        height=5, _bottom_lines >= 8 → scroll_end < 1 → 全屏清除降级路径。
        """
        mock_term = _mock_terminal(width=80, height=5)

        self.bb.set_subagent_frame(["line1", "line2", "line3"])

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term), \
             patch.object(self.bb, '_format_status', return_value="STATUS"):
            # 不应抛出异常
            self.bb.force_redraw()

        output = out.getvalue()
        # 降级路径：全屏清除 1-5 行
        for r in range(1, 6):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"降级路径应清除终端第 {r} 行")
        # 降级路径中不应渲染 subagent 面板内容
        self.assertNotIn("line1", output,
                         "终端过小时不应渲染 subagent 面板内容")

    # ── 场景 6：补全弹窗与 subagent 面板共存 ────────────

    def test_subagent_panel_with_completion_popup(self):
        """补全弹窗与 subagent 面板共存时布局正确（两者不重叠）。"""
        mock_term = _mock_terminal(width=80, height=30)

        # 设置 subagent 面板
        self.bb.set_subagent_frame(["sub_line1", "sub_line2"])
        # 设置补全弹窗可见
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 4
        self.bb._completion._title = "补全"
        self.bb._completion._items = ["item_a", "item_b"]
        self.bb._completion._texts = ["item_a", "item_b"]
        self.bb._completion._idx = 0

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term), \
             patch.object(self.bb, '_format_status', return_value="STATUS"):
            self.bb.force_redraw()

        output = out.getvalue()

        # 两者内容都应在输出中
        self.assertIn("sub_line1", output, "输出应包含 subagent 面板行")
        self.assertIn("sub_line2", output, "输出应包含 subagent 面板行")
        self.assertIn("补全", output, "输出应包含补全弹窗标题")

        # subagent 面板行在补全弹窗之前（subagent 在状态行上方，补全在输入区上方）
        sub_pos = output.index("sub_line2")
        comp_pos = output.index("补全")
        self.assertLess(sub_pos, comp_pos,
                        "subagent 面板应在补全弹窗之前（subagent 在分隔线下方，补全在输入区上方）")


if __name__ == "__main__":
    unittest.main()
