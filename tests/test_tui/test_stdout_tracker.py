"""tests for src/ui/_stdout_tracker.py — _track() 序列处理顺序。

重点验证 P1-4 修复：光标恢复序列（\\0338 / \\033[u）按数据流实际顺序处理，
而非提前在数据包开头全局检测。
"""
from __future__ import annotations

import io
import unittest

from src.ui._stdout_tracker import _StdoutLineTracker


def _make_tracker(scroll_end: int = 5) -> _StdoutLineTracker:
    """创建测试用 tracker（scroll_end=5，行>5 为底部栏区域）。"""
    t = _StdoutLineTracker(io.StringIO())
    t.set_scroll_end(scroll_end)
    return t


class TestCursorRestoreOrder(unittest.TestCase):
    """验证 \0338 / \033[u 按数据流顺序处理。"""

    def test_restore_after_positioning(self):
        """\033[35;1H text \0338 — text 应被过滤（在底部栏），\0338 在 text 之后退出。"""
        t = _make_tracker(scroll_end=5)
        # 光标定位到行 35（底部栏），写入 text，然后恢复光标
        t.write("\033[35;1Hbottom_text\0338")
        # bottom_text 在底部栏区域 → 不应进入 ring buffer
        self.assertEqual(len(t._ring), 0)

    def test_restore_before_positioning(self):
        """\0338 normal_text \033[35;1H — normal_text 应被追踪。"""
        t = _make_tracker(scroll_end=5)
        t.write("\0338normal_text\n\033[35;1H")
        # normal_text 在内容区 → 应进入 ring buffer
        self.assertIn("normal_text", list(t._ring))

    def test_positioning_restore_positioning(self):
        """交替序列：定位(底部栏) → 恢复 → 定位(底部栏)。"""
        t = _make_tracker(scroll_end=5)
        t.write("\033[35;1Hbottom1\0338normal1\n\033[36;1Hbottom2")
        # 只有 normal1 应被追踪
        self.assertIn("normal1", list(t._ring))
        self.assertNotIn("bottom1", list(t._ring))
        self.assertNotIn("bottom2", list(t._ring))

    def test_scrc_restore(self):
        """\033[u 恢复序列。"""
        t = _make_tracker(scroll_end=5)
        t.write("\033[35;1Hbottom_text\033[u")
        self.assertEqual(len(t._ring), 0)

    def test_restore_exits_bottom_bar(self):
        """恢复后后续文本应被追踪。"""
        t = _make_tracker(scroll_end=5)
        t.write("\033[35;1Hbottom\0338normal\n")
        self.assertIn("normal", list(t._ring))
        self.assertNotIn("bottom", list(t._ring))


class TestMultiplePackets(unittest.TestCase):
    """跨数据包时序。"""

    def test_split_positioning_and_text(self):
        """光标定位和文本在不同 write 调用中。"""
        t = _make_tracker(scroll_end=5)
        t.write("\033[35;1H")
        t.write("bottom_text\n")
        self.assertEqual(len(t._ring), 0)  # 底部栏内容不追踪

    def test_split_restore_and_text(self):
        """恢复序列和文本在不同 write 调用中。"""
        t = _make_tracker(scroll_end=5)
        t.write("\033[35;1H")  # 进入底部栏
        t.write("\0338")        # 恢复
        t.write("normal_text\n")  # 内容区文本
        self.assertIn("normal_text", list(t._ring))

    def test_normal_text_tracked(self):
        """正常内容区文本应被追踪。"""
        t = _make_tracker(scroll_end=5)
        t.write("hello world\n")
        self.assertIn("hello world", list(t._ring))

    def test_partial_line_accumulation(self):
        """不完整行累积。"""
        t = _make_tracker(scroll_end=5)
        t.write("hello ")
        t.write("world\n")
        self.assertIn("hello world", list(t._ring))


class TestScrollEndDisabled(unittest.TestCase):
    """scroll_end < 1 时禁用追踪。"""

    def test_disabled_no_tracking(self):
        t = _make_tracker(scroll_end=0)
        t.write("hello\nworld\n")
        self.assertEqual(len(t._ring), 0)

    def test_disabled_with_positioning(self):
        t = _make_tracker(scroll_end=0)
        t.write("\033[35;1Htext\0338normal\n")
        self.assertEqual(len(t._ring), 0)


class TestRingBuffer(unittest.TestCase):
    """环形缓冲区边界。"""

    def test_max_lines(self):
        t = _make_tracker(scroll_end=100)
        for i in range(350):
            t.write(f"line{i}\n")
        # maxlen=300, should only keep last 300
        self.assertEqual(len(t._ring), 300)
        self.assertIn("line349", list(t._ring))
        self.assertNotIn("line0", list(t._ring))


if __name__ == "__main__":
    unittest.main()
