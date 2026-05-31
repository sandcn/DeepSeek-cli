"""_BottomBar 光标定位测试 — 验证 cursor_pos 在 refresh() 中的正确传播。

测试策略：
  模拟 _BottomBar 处于激活状态，直接调用 refresh() 后检查 _input_cursor_pos
  是否正确更新。不涉及终端 I/O（ANSI 输出写入 devnull）。
"""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import patch

from src.ui._bottom_bar import _BottomBar


class TestBottomBarCursorPos(unittest.TestCase):
    """验证 _input_cursor_pos 在 refresh() 各路径中的正确更新。

    核心场景：
      1. 纯光标移动（text_changed=False, status_changed=True）→ 独立 if 分支更新
      2. 光标移动+状态变化（text_changed=False, status_changed=True）→ 同上
      3. cursor_pos=-1（末尾定位）→ 不更新 _input_cursor_pos
      4. 文本变化+光标移动 → _input_cursor_pos 同步更新
    """

    def setUp(self):
        self.bb = _BottomBar()
        # 激活底部栏（模拟 setup 后的状态）
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        # 模拟已输入的文本和光标位置
        self.bb._last_text = "hello world"
        self.bb._input_cursor_pos = 11  # 末尾
        self.bb._last_cursor_pos = 11
        # 模拟流式状态活跃（_format_status() 返回非空）
        self.bb._status_active = True
        self.bb._model_name = "test-model"
        self.bb._last_status = "test-model ◉"
        # 禁用终端 I/O
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    # ── 场景 1：纯光标移动（文本不变，状态活跃） ──────────

    def test_cursor_move_during_streaming(self):
        """流式输出期间纯光标移动 → _input_cursor_pos 应更新。"""
        # 模拟用户按 ← 将光标从末尾(11)移到 "world" 的 'w'(6)
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉  5t"):
            self.bb.refresh("hello world", cursor_pos=6)

        self.assertEqual(self.bb._input_cursor_pos, 6,
                         "流式活跃时光标移动应更新 _input_cursor_pos")

    def test_cursor_move_after_streaming(self):
        """非流式期间纯光标移动 → _input_cursor_pos 应更新。"""
        self.bb._status_active = False
        self.bb._last_status = ""

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.refresh("hello world", cursor_pos=6)

        self.assertEqual(self.bb._input_cursor_pos, 6,
                         "非流式时光标移动应更新 _input_cursor_pos")

    # ── 场景 2：光标移动 + 状态变化 ────────────────────

    def test_cursor_move_with_status_change(self):
        """流式状态变化 + 光标移动 → _input_cursor_pos 应更新（修复的核心场景）。"""
        # _format_status() 返回新状态 → status_changed=True
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb.refresh("hello world", cursor_pos=3)

        self.assertEqual(self.bb._input_cursor_pos, 3,
                         "状态变化 + 光标移动应正确更新 _input_cursor_pos")

    # ── 场景 3：cursor_pos=-1（末尾定位） ─────────────

    def test_cursor_neg_one_preserves_position(self):
        """cursor_pos=-1 不更新 _input_cursor_pos（保持旧值）。"""
        self.bb._input_cursor_pos = 5  # 用户之前移动到了位置 5

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb.refresh("hello world", cursor_pos=-1)

        self.assertEqual(self.bb._input_cursor_pos, 5,
                         "cursor_pos=-1 应保持 _input_cursor_pos 不变")

    # ── 场景 4：文本变化 + 光标移动 ────────────────────

    def test_text_change_with_cursor_pos(self):
        """文本变化 + 光标移动 → _input_cursor_pos 应更新。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb.refresh("hello world!", cursor_pos=12)

        self.assertEqual(self.bb._input_cursor_pos, 12,
                         "文本变化时光标应更新到新位置")

    # ── 场景 5：文本变化 + cursor_pos=-1（键入字符，光标在末尾） ──

    def test_text_change_with_cursor_neg_one(self):
        """键入字符（text_changed=True, cursor_pos=-1）→ _input_cursor_pos 应更新到文本末尾。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            # cursor_pos=-1 表示"定位到末尾"
            self.bb.refresh("hello world!", cursor_pos=-1)

        # text_changed=True 路径中：self._input_cursor_pos = cursor_pos = -1
        self.assertEqual(self.bb._input_cursor_pos, -1,
                         "cursor_pos=-1 的键入应将 _input_cursor_pos 设为 -1")

    # ── 场景 6：refresh 轻量路径（仅光标移动，无锁） ─────

    def test_light_path_cursor_move(self):
        """轻量路径（仅光标移动，文本/状态/尺寸未变）→ _input_cursor_pos 应更新。"""
        self.bb._last_status = "test-model ◉"  # 与 mock 返回值一致
        self.bb._last_rendered_text = "hello world"

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉"):
            self.bb.refresh("hello world", cursor_pos=3)

        self.assertEqual(self.bb._input_cursor_pos, 3,
                         "轻量路径光标移动应更新 _input_cursor_pos")



class TestBottomBarFormatStatus(unittest.TestCase):
    """验证 _format_status() 在流式/非流式下的返回值。

    核心场景：
      1. 流式活跃（_status_active=True, _tool_count>0）→ 返回含统计信息的完整状态行
      2. 非流式空闲（_status_active=False），即使 _tool_count>0 → 仅返回模型名
      3. 非流式空闲（_status_active=False），_tool_count==0 → 仅返回模型名
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._model_name = "test-model"

    def test_streaming_active_shows_full_stats(self):
        """流式输出期间 _status_active=True → 返回含统计信息的完整状态行。"""
        self.bb._status_active = True
        self.bb._tool_count = 3
        self.bb._tool_fail_count = 1

        # mock _get_snapshot 返回模拟的统计数据
        mock_snap = {
            "total_tokens": 1500,
            "elapsed_seconds": 12.5,
            "per_second_speed": 25.3,
        }

        with patch("src.ui._bottom_bar._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        # 应包含模型名 + 统计信息
        self.assertIn("test-model", result)
        self.assertIn("t", result)      # token 数
        self.assertIn("12.5s", result)  # 耗时
        self.assertIn("25.3t/s", result)  # 速率

    def test_streaming_inactive_hides_stats_even_with_tool_count(self):
        """非流式空闲 _status_active=False，即使 _tool_count>0 → 仅模型名。"""
        self.bb._status_active = False
        self.bb._tool_count = 3
        self.bb._tool_fail_count = 1

        # mock snapshot 有数据，但 _status_active=False 时应跳过
        with patch("src.ui._bottom_bar._get_snapshot", return_value=None):
            result = self.bb._format_status()

        # 应包含模型名（含 ANSI 颜色码）
        self.assertIn("test-model", result)
        # 不应包含统计关键词
        self.assertNotIn("t/s", result)  # 速率统计不应出现

    def test_streaming_inactive_no_tool_count(self):
        """非流式空闲 _status_active=False 且 _tool_count==0 → 仅模型名。"""
        self.bb._status_active = False
        self.bb._tool_count = 0
        self.bb._tool_fail_count = 0

        with patch("src.ui._bottom_bar._get_snapshot", return_value=None):
            result = self.bb._format_status()

        # 应包含模型名（含 ANSI 颜色码），不含统计信息
        self.assertIn("test-model", result)
        self.assertNotIn("t/s", result)

    def test_streaming_active_with_tool_count_zero_but_snapshot_has_data(self):
        """流式活跃 _status_active=True，_tool_count=0，但 snapshot 有历史数据 → 应显示统计。"""
        self.bb._status_active = True
        self.bb._tool_count = 0
        self.bb._tool_fail_count = 0

        mock_snap = {
            "total_tokens": 500,
            "elapsed_seconds": 5.0,
            "per_second_speed": 10.0,
        }

        with patch("src.ui._bottom_bar._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        # 应包含模型名 + 统计
        self.assertIn("test-model", result)
        self.assertIn("t", result)      # token
        self.assertIn("5.0s", result)   # 耗时
        self.assertIn("10.0t/s", result)  # 速率

    def test_streaming_active_no_snapshot_no_tool_count(self):
        """流式活跃 _status_active=True，但 snapshot 无数据且 _tool_count=0 → 仅模型名。"""
        self.bb._status_active = True
        self.bb._tool_count = 0
        self.bb._tool_fail_count = 0

        # snapshot 返回空数据
        mock_snap = {
            "total_tokens": 0,
            "elapsed_seconds": 0.0,
            "per_second_speed": 0.0,
        }

        with patch("src.ui._bottom_bar._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        self.assertIn("test-model", result)
        self.assertNotIn("t/s", result)


if __name__ == "__main__":
    unittest.main()
