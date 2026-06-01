"""_BottomBar 光标定位测试 — 验证 cursor_pos 在 refresh() 中的正确传播。

测试策略：
  模拟 _BottomBar 处于激活状态，直接调用 refresh() 后检查 _input_cursor_pos
  是否正确更新。不涉及终端 I/O（ANSI 输出写入 devnull）。
"""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

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


class TestBottomBarLastScrollEnd(unittest.TestCase):
    """验证 _last_scroll_end 缓存在 DECSTBM 设置处的正确同步。

    核心场景：
      1. _last_scroll_end 初始值为 0
      2. setup() 后 _last_scroll_end 等于 height - _bottom_lines
      3. ensure_cursor_in_upper() 使用 _last_scroll_end 而非动态计算
      4. sync_bottom_lines() 在 _bottom_lines 变化时同步 DECSTBM
      5. sync_bottom_lines() 在无变化时静默跳过
    """

    def setUp(self):
        self.bb = _BottomBar()
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_initial_value_is_zero(self):
        """_last_scroll_end 初始值为 0。"""
        self.assertEqual(self.bb._last_scroll_end, 0,
                         "_last_scroll_end 初始值应为 0")

    def test_setup_syncs_last_scroll_end(self):
        """setup() 后 _last_scroll_end 应等于 height - _bottom_lines。"""
        self.bb._cached_height = 30
        self.bb._cached_width = 80

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch("src.ui._bottom_bar.query_terminal_size", return_value=(80, 30)):
            self.bb.setup()

        expected = 30 - (2 + max(3, 0))  # height - (_BOTTOM_LINES + 0 = 5) = 25
        self.assertEqual(self.bb._last_scroll_end, 25,
                         "setup() 后 _last_scroll_end 应为 25 (30-5)")

    def test_ensure_cursor_upper_uses_cached_value(self):
        """ensure_cursor_in_upper() 使用 _last_scroll_end 而非动态计算。"""
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_scroll_end = 25  # 模拟 setup 后的值
        self.bb._last_text = "x" * 300  # 长文本使 _bottom_lines 很大

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out):
            self.bb.ensure_cursor_in_upper()

        # 应输出 \033[25;1H（用缓存值 25），而非动态计算的更小值
        output = out.getvalue()
        self.assertIn("\033[25;1H", output,
                      "ensure_cursor_in_upper 应使用 _last_scroll_end=25 而非动态值")

    def test_ensure_cursor_upper_fallback_when_zero(self):
        """_last_scroll_end=0 时降级到 terminal height。"""
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_scroll_end = 0  # 未初始化

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out):
            self.bb.ensure_cursor_in_upper()

        output = out.getvalue()
        self.assertIn("\033[30;1H", output,
                      "_last_scroll_end=0 时应降级到 height=30")

    def test_sync_bottom_lines_updates_decstbm(self):
        """sync_bottom_lines() 在 _bottom_lines 变化时同步 DECSTBM。"""
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_scroll_end = 25  # 旧值（30-5）
        # 让 _bottom_lines 变大（模拟补全弹窗弹出）
        self.bb._completion_popup_height = 6

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out):
            self.bb.sync_bottom_lines()

        # _bottom_lines = 2 + max(3, 0) + 6 = 11
        # scroll_end = 30 - 11 = 19
        output = out.getvalue()
        self.assertIn("\033[1;19r", output,
                      "sync_bottom_lines 应输出 DECSTBM \\033[1;19r")
        self.assertEqual(self.bb._last_scroll_end, 19,
                         "sync_bottom_lines 应更新 _last_scroll_end 到 19")

    def test_sync_bottom_lines_skips_when_unchanged(self):
        """sync_bottom_lines() 在 _bottom_lines 未变时静默跳过。"""
        self.bb._active = True
        self.bb._cached_height = 30
        self.bb._cached_width = 80
        self.bb._last_scroll_end = 25  # 30 - 5 = 25，与当前 _bottom_lines 一致

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out):
            self.bb.sync_bottom_lines()

        output = out.getvalue()
        self.assertEqual(output, "",
                         "_bottom_lines 未变时 sync_bottom_lines 不应输出 ANSI 序列")


class TestDrainQueueSyncBottomLines(unittest.TestCase):
    """验证 _drain_queue() Stage 1 非 resize 时调用 sync_bottom_lines()。"""

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.mock_bb.is_status_active = False
        self.mock_bb.is_resize_pending = False
        self.mock_bb._active = True
        self.mock_bb._setup_height = 30
        self.mock_bb._last_bottom_lines = 5
        self.mock_bb._bottom_lines = 5
        self.mock_bb._completion_popup_height = 0
        self.mock_bb._last_scroll_end = 25  # 模拟已缓存的值
        self.mock_bb.get_cursor_info.return_value = ("", 0, 24, 80)
        self.mock_bb._cursor_visual_pos_from_cache.return_value = (0, 0)
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb)
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _enqueue_cmd(self):
        from src.chat_ui._const import RenderCommand
        self.engine.push_cmd((RenderCommand.NOTIFICATION, "test"))

    def test_not_resized_calls_sync_bottom_lines(self):
        """resized=False 时 sync_bottom_lines 应在 ensure_cursor_upper 之前被调用。"""
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = False

        with patch("src.chat_ui._state._active_parallel_display", None), \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        # 验证 sync_bottom_lines 被调用过
        self.mock_bb.sync_bottom_lines.assert_called()
        # 验证 ensure_cursor_upper 也被调用（在 sync_bottom_lines 之后）
        self.mock_bb.ensure_cursor_in_upper.assert_called()
        # 验证调用顺序：sync_bottom_lines 先于 ensure_cursor_upper
        call_order = self.mock_bb.method_calls
        sync_idx = next(i for i, c in enumerate(call_order)
                        if c[0] == 'sync_bottom_lines')
        cursor_idx = next(i for i, c in enumerate(call_order)
                          if c[0] == 'ensure_cursor_in_upper')
        self.assertLess(sync_idx, cursor_idx,
                        "sync_bottom_lines 应在 ensure_cursor_upper 之前调用")

    def test_resized_skips_sync_bottom_lines(self):
        """resized=True 时 sync_bottom_lines 不应被调用。"""
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = True
        # 模拟 resize 场景必要的属性值，避免 Fix A 逻辑报错
        self.mock_bb._setup_height = 30
        self.mock_bb._last_bottom_lines = 5
        self.mock_bb._bottom_lines = 5
        self.mock_bb._active = True
        self.mock_bb._term_height.return_value = 35

        with patch("src.chat_ui._state._active_parallel_display", None), \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        self.mock_bb.sync_bottom_lines.assert_not_called()
        self.mock_bb.ensure_cursor_in_upper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
