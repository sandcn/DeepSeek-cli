"""tests for src/ui/ansi.py — visual_width / truncate_ansi_* 正确性。

重点验证 _char_width（wcswidth）修复后的行为：
  - 零宽字符 (U+200B, U+200D) 宽度=0
  - 组合标记 (U+0300) 宽度=0
  - CJK 字符宽度=2
  - ASCII 字符宽度=1
  - truncate_ansi_sgr 按 wcswidth 截断（CJK 占 2 列）
  - truncate_ansi_line 按视觉宽度截断
  - truncate_ansi_visual 按视觉宽度截断
"""
from __future__ import annotations

import unittest

from src.ui.ansi import (
    visual_width,
    truncate_ansi_visual,
    truncate_ansi_sgr,
    truncate_ansi_line,
    strip_ansi,
    RESET,
)


# ── visual_width ──────────────────────────────────────

class TestVisualWidth(unittest.TestCase):

    def test_ascii(self):
        self.assertEqual(visual_width("hello"), 5)

    def test_cjk(self):
        self.assertEqual(visual_width("你好"), 4)

    def test_mixed_ascii_cjk(self):
        self.assertEqual(visual_width("a你"), 3)

    def test_zero_width_space(self):
        self.assertEqual(visual_width("a\u200bb"), 2)

    def test_zero_width_joiner(self):
        self.assertEqual(visual_width("a\u200db"), 2)

    def test_combining_mark(self):
        self.assertEqual(visual_width("a\u0300b"), 2)

    def test_ansi_stripped(self):
        self.assertEqual(visual_width("\033[31mhello\033[0m"), 5)

    def test_ansi_with_cjk(self):
        self.assertEqual(visual_width("\033[31m你好\033[0m"), 4)

    def test_empty(self):
        self.assertEqual(visual_width(""), 0)


# ── truncate_ansi_visual ──────────────────────────────

class TestTruncateAnsiVisual(unittest.TestCase):

    def test_no_truncation_needed(self):
        self.assertEqual(truncate_ansi_visual("hello", 10), "hello")

    def test_ascii_truncation(self):
        result = truncate_ansi_visual("hello world", 5)
        self.assertIn("hell", result)
        self.assertIn("…", result)

    def test_cjk_truncation(self):
        result = truncate_ansi_visual("你好世界测试", 5)
        # 你好=4 columns, 5-1=4, so should fit "你好" (4) + …
        self.assertIn("你好", result)
        self.assertIn("…", result)

    def test_cjk_not_over_truncated(self):
        result = truncate_ansi_visual("你好世界", 5)
        # 你好=4, 世=2 would be 6 > 4, so only 你好
        self.assertIn("你好", result)
        self.assertNotIn("世", result)

    def test_zero_width_not_counted(self):
        result = truncate_ansi_visual("a\u200bb\u200bc\u200bd", 3)
        # All 4 visible chars have 0-width joiners, visual width = 4
        # max_visual=3, should truncate
        self.assertIn("…", result)

    def test_preserves_ansi(self):
        result = truncate_ansi_visual("\033[31mhello world\033[0m", 5)
        self.assertIn("\033[31m", result)
        self.assertIn(RESET, result)


# ── truncate_ansi_sgr ─────────────────────────────────

class TestTruncateAnsiSgr(unittest.TestCase):

    def test_no_truncation_needed(self):
        result = truncate_ansi_sgr("hello", 10)
        self.assertEqual(result, "hello\033[0m")

    def test_ascii_truncation(self):
        result = truncate_ansi_sgr("hello world", 5)
        self.assertTrue(result.endswith("\033[0m"))
        # Should contain "hello" (5 chars)
        self.assertIn("hello", result)

    def test_cjk_truncation_by_width(self):
        # "你好" = 4 columns, max_width=4 → fits
        result = truncate_ansi_sgr("你好", 4)
        self.assertIn("你好", result)

    def test_cjk_truncation_exceeds(self):
        # "你好世界" = 8 columns, max_width=5 → 你好(4) fits, 世(2) would be 6>5
        result = truncate_ansi_sgr("你好世界", 5)
        self.assertIn("你好", result)
        # 世 should not be in the truncated part (2+4=6 > 5)
        plain = strip_ansi(result)
        self.assertNotIn("世", plain)

    def test_from_end_cjk(self):
        # "你好世界" = 8 columns, take last 4 columns = "世界"
        result = truncate_ansi_sgr("你好世界", 4, from_end=True)
        plain = strip_ansi(result)
        self.assertIn("世界", plain)
        self.assertNotIn("你好", plain)

    def test_from_end_ascii(self):
        result = truncate_ansi_sgr("hello world", 5, from_end=True)
        plain = strip_ansi(result)
        self.assertIn("world", plain)

    def test_sgr_preserved(self):
        result = truncate_ansi_sgr("\033[31mhello world\033[0m", 5)
        self.assertIn("\033[31m", result)
        self.assertTrue(result.endswith("\033[0m"))

    def test_zero_width_not_counted(self):
        # "a\u200bb" = visual width 2, max_width=2 → fits
        result = truncate_ansi_sgr("a\u200bb", 2)
        self.assertIn("a", strip_ansi(result))
        self.assertIn("b", strip_ansi(result))


# ── truncate_ansi_line ────────────────────────────────

class TestTruncateAnsiLine(unittest.TestCase):

    def test_no_truncation_needed(self):
        self.assertEqual(truncate_ansi_line("hello", 10), "hello")

    def test_ascii_truncation(self):
        result = truncate_ansi_line("hello world", 8)
        self.assertTrue(result.endswith(RESET + "..."))
        self.assertIn("hello", result)

    def test_cjk_truncation_by_width(self):
        # "你好世界" = 8 columns, max_width=6 → visible_limit=3
        # 你(2) <= 3, 好(2+2=4) > 3 → only 你
        result = truncate_ansi_line("你好世界", 6)
        self.assertTrue(result.endswith(RESET + "..."))
        plain = strip_ansi(result)
        self.assertIn("你", plain)
        self.assertNotIn("世", plain)

    def test_cjk_fits_exactly(self):
        # "你好" = 4 columns, max_width=4 → no truncation
        result = truncate_ansi_line("你好", 4)
        self.assertEqual(result, "你好")

    def test_ansi_preserved(self):
        result = truncate_ansi_line("\033[31mhello world\033[0m", 8)
        self.assertIn("\033[31m", result)
        self.assertTrue(result.endswith(RESET + "..."))


if __name__ == "__main__":
    unittest.main()
