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


if __name__ == "__main__":
    unittest.main()
