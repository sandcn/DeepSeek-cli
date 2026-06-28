"""_BottomBar 光标定位测试 — 验证 cursor_pos 在 force_redraw() 中的正确传播。

测试策略：
  模拟 _BottomBar 处于激活状态，直接设置输入文本后调用 force_redraw() 检查
  _input_cursor_pos 是否正确更新。不涉及终端 I/O（ANSI 输出写入 devnull）。
"""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

from src.ui._bottom_bar import _BottomBar
from src.ui._stdout_tracker import _StdoutLineTracker


def _mock_terminal(width=80, height=30):
    """创建模拟 Blessed Terminal 对象，返回指定的 width/height。"""
    mock_term = MagicMock()
    mock_term.width = width
    mock_term.height = height
    # move_xy 返回实际的 ANSI 序列
    mock_term.move_xy.side_effect = lambda x, y: f"\033[{y+1};{x+1}H"
    mock_term.clear_eol = "\033[K"
    return mock_term


class TestBottomBarCursorPos(unittest.TestCase):
    """验证 _input_cursor_pos 在设置文本+force_redraw() 各路径中的正确更新。

    核心场景：
      1. 纯光标移动 → 设置 _input_cursor_pos 后 force_redraw()
      2. 光标移动+状态变化 → 同上
      3. cursor_pos=-1（末尾定位）→ 设置 _input_cursor_pos 为文本长度
      4. 文本变化+光标移动 → 同步更新
    """

    def setUp(self):
        self.bb = _BottomBar()
        # 激活底部栏（模拟 setup 后的状态）
        self.bb._active = True
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
        """流式输出期间设置光标位置 → _input_cursor_pos 应正确更新。"""
        # 模拟用户按 ← 将光标从末尾(11)移到 "world" 的 'w'(6)
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉  5t"):
            self.bb._last_text = "hello world"
            self.bb._input_cursor_pos = 6
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 6,
                         "设置光标位后 force_redraw() 应保持 _input_cursor_pos")

    def test_cursor_move_after_streaming(self):
        """非流式期间设置光标位置 → _input_cursor_pos 应正确更新。"""
        self.bb._status_active = False
        self.bb._last_status = ""

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb._last_text = "hello world"
            self.bb._input_cursor_pos = 6
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 6,
                         "非流式时设置光标位应保持 _input_cursor_pos")

    # ── 场景 2：光标移动 + 状态变化 ────────────────────

    def test_cursor_move_with_status_change(self):
        """状态变化 + 设置光标位置 → _input_cursor_pos 应正确更新。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb._last_text = "hello world"
            self.bb._input_cursor_pos = 3
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 3,
                         "状态变化 + 光标移动应正确保持 _input_cursor_pos")

    # ── 场景 3：文本不变，保持旧光标位置 ─────────────

    def test_cursor_pos_preserved_when_not_set(self):
        """不设置 _input_cursor_pos 时保持旧值。"""
        self.bb._input_cursor_pos = 5  # 用户之前移动到了位置 5

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb._last_text = "hello world"
            self.bb.force_redraw()  # 不改变 _input_cursor_pos

        self.assertEqual(self.bb._input_cursor_pos, 5,
                         "不设置光标位时应保持原值")

    # ── 场景 4：文本变化 + 光标移动 ────────────────────

    def test_text_change_with_cursor_pos(self):
        """文本变化 + 设置光标位置 → _input_cursor_pos 应更新。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb._last_text = "hello world!"
            self.bb._input_cursor_pos = 12
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 12,
                         "文本变化时光标应更新到新位置")

    # ── 场景 5：文本变化，光标在末尾 ─────────────────

    def test_text_change_cursor_at_end(self):
        """文本变化，光标在末尾 → _input_cursor_pos 应为文本长度。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉ 10t"):
            self.bb._last_text = "hello world!"
            self.bb._input_cursor_pos = 12  # 即 len("hello world!")
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 12,
                         "文本变化+末尾光标应正确保持")

    # ── 场景 6：force_redraw 布局未变时快速跳过 ─────

    def test_force_redraw_skips_when_unchanged(self):
        """布局和状态均未变时 force_redraw 应跳过全量重绘。"""
        self.bb._last_status = "test-model ◉"
        self.bb._last_rendered_text = "hello world"
        self.bb._last_text = "hello world"
        self.bb._last_bottom_lines = self.bb._bottom_lines
        self.bb._last_height = self.bb._term_height()  # 需同步终端高度才能命中快速路径

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch.object(self.bb, '_format_status', return_value="test-model ◉"):
            self.bb.force_redraw()

        self.assertEqual(out.getvalue(), "",
                         "布局和状态未变时 force_redraw 不应输出 ANSI 序列")



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

        with patch("src.ui._bottom_bar_status._get_snapshot", return_value=lambda: mock_snap):
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
        with patch("src.ui._bottom_bar_status._get_snapshot", return_value=None):
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

        with patch("src.ui._bottom_bar_status._get_snapshot", return_value=None):
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

        with patch("src.ui._bottom_bar_status._get_snapshot", return_value=lambda: mock_snap):
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

        with patch("src.ui._bottom_bar_status._get_snapshot", return_value=lambda: mock_snap):
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

        mock_term = _mock_terminal(width=80, height=30)
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            self.bb.setup()

        expected = 30 - (2 + max(3, 0))  # height - (_BOTTOM_LINES + 0 = 5) = 25
        self.assertEqual(self.bb._last_scroll_end, 25,
                         "setup() 后 _last_scroll_end 应为 25 (30-5)")

    def test_ensure_cursor_upper_uses_cached_value(self):
        """ensure_cursor_in_upper() 使用 _last_scroll_end 而非动态计算。"""
        self.bb._active = True
        self.bb._last_scroll_end = 25  # 模拟 setup 后的值
        self.bb._last_text = "x" * 300  # 长文本使 _bottom_lines 很大

        # Blessed 在非 TTY 环境下返回空字符串，需 patch get_terminal
        mock_term = _mock_terminal(width=80, height=30)
        with patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            out = io.StringIO()
            old = sys.__stdout__
            sys.__stdout__ = out
            try:
                self.bb.ensure_cursor_in_upper()
            finally:
                sys.__stdout__ = old

        # 应输出 \033[25;1H（用缓存值 25），而非动态计算的更小值
        output = out.getvalue()
        self.assertIn("\033[25;1H", output,
                      "ensure_cursor_in_upper 应使用 _last_scroll_end=25 而非动态值")

    def test_ensure_cursor_upper_fallback_when_zero(self):
        """_last_scroll_end=0 时降级到 terminal height。"""
        self.bb._active = True
        self.bb._last_scroll_end = 0  # 未初始化

        mock_term = _mock_terminal(width=80, height=30)
        with patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            out = io.StringIO()
            with patch.object(sys, '__stdout__', out):
                self.bb.ensure_cursor_in_upper()

        output = out.getvalue()
        self.assertIn("\033[30;1H", output,
                      "_last_scroll_end=0 时应降级到 height=30")

    def test_sync_bottom_lines_updates_decstbm(self):
        """sync_bottom_lines() 在 _bottom_lines 变化时同步 DECSTBM。"""
        self.bb._active = True
        self.bb._last_scroll_end = 25  # 旧值（30-5）
        # 让 _bottom_lines 变大（模拟补全弹窗弹出）
        self.bb._completion_popup_height = 6

        mock_term = _mock_terminal(width=80, height=30)
        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            self.bb.sync_bottom_lines()

        # _bottom_lines = 2 + max(3, 0) + 6 = 11
        # scroll_end = 30 - 11 = 19
        output = out.getvalue()
        self.assertIn("\033[1;19r", output,
                      "sync_bottom_lines 应输出 DECSTBM \\033[1;19r")
        self.assertEqual(self.bb._last_scroll_end, 19,
                         "sync_bottom_lines 应更新 _last_scroll_end 到 19")

    def test_sync_bottom_lines_skips_when_unchanged(self):
        """sync_bottom_lines() 在 _bottom_lines 未变且终端高度未变时静默跳过。"""
        self.bb._active = True
        self.bb._last_scroll_end = 25  # 30 - 5 = 25，与当前 _bottom_lines 一致
        self.bb._last_sync_height = 30  # 终端高度未变

        mock_term = _mock_terminal(width=80, height=30)
        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            self.bb.sync_bottom_lines()

        output = out.getvalue()
        self.assertEqual(output, "",
                         "_bottom_lines 未变时 sync_bottom_lines 不应输出 ANSI 序列")

    def test_sync_bottom_lines_shrink_clears_interval(self):
        """终端缩小后 sync_bottom_lines 清除 scroll_end+1 到 old_scroll 整个区间。

        Bug 修复验证：缩小后清除全部将变为底部栏区域的行（而非仅清除单一边界行），
        消除底部栏刷新（输入）路径中旧内容在 force_redraw 前的残留。
        """
        self.bb._active = True
        # 模拟旧状态：scroll_end=25（30-5）
        self.bb._last_scroll_end = 25
        self.bb._last_sync_height = 30
        self.bb._last_text = ""

        # 终端缩小到 25 行
        mock_term = _mock_terminal(width=80, height=25)
        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            self.bb.sync_bottom_lines()

        output = out.getvalue()
        # scroll_end = 25 - 5 = 20, old_scroll = 25
        # 应清除行 21-25 整个区间
        for r in range(21, 26):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"终端缩小后应清除旧内容残留行 {r}")
        # DECSTBM 应更新为 (1, 20)
        self.assertIn("\033[1;20r", output,
                      "终端缩小后 DECSTBM 应更新为 (1, 20)")


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

        with \
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

    @unittest.skip("check_resize 已从 RenderEngine 移除，sync_bottom_lines 始终在 _phase_render 中调用")
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

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        self.mock_bb.sync_bottom_lines.assert_not_called()
        self.mock_bb.ensure_cursor_in_upper.assert_not_called()


class TestApplyScrollDeltaOrdering(unittest.TestCase):
    """验证 2026-06-12 修复后的 force_redraw 行为。

    2026-06-12 修复：移除 SU（底部栏扩大时不再上滚内容）。
    SU 在 DECSTBM 内无 scrollback 缓冲，滚出顶部的行永久丢失，
    改为直接缩小滚动区域，让弹窗覆盖底部内容区少数行。
    底部栏缩小时直接清除回收区域行（不使用 SD 下滚）。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._setup_height = 30
        self.bb._setup_width = 40
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 3  # 最小底部行数
        self.bb._last_rendered_text = "test"
        self.bb._last_height = 30  # force_redraw 哨兵
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _capture_ansi_order(self, method_call):
        """调用指定方法，捕获 ANSI 输出序列（终端尺寸固定为 80x30）。

        同时 mock get_terminal() 的 height/width 属性以确保测试可重复。
        """
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)):
            # ★ 同时 mock get_terminal() 的 height/width，确保 _term_height/Width 返回 mock 值
            from unittest.mock import MagicMock
            mock_term = MagicMock()
            mock_term.height = 30
            mock_term.width = 80
            # move_xy 生成原始 ANSI（确保测试可重复，不依赖 blessed 实现）
            mock_term.move_xy = lambda x, y: f"\033[{y + 1};{x + 1}H"
            mock_term.clear_eol = "\033[K"
            with patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
                method_call()
        return buf.getvalue()

    def assert_ansi_before(self, output, first_seq, second_seq, msg=None):
        """断言 first_seq 在 second_seq 之前出现在 output 中。"""
        pos1 = output.find(first_seq)
        pos2 = output.find(second_seq)
        self.assertNotEqual(pos1, -1, f"未找到序列 {first_seq!r}")
        self.assertNotEqual(pos2, -1, f"未找到序列 {second_seq!r}")
        self.assertLess(pos1, pos2,
                        msg or f"{first_seq!r} 应在 {second_seq!r} 之前")

    def test_force_redraw_expand_no_su(self):
        """★ 2026-06-12 修复: force_redraw 在 delta > 0 时不再输出 SU 上滚序列。

        移除 SU（Scroll Up）以避免丢失上屏顶部内容。
        底部栏扩大时，新划入底部栏的区域（原内容区底部行）直接由
        _draw_input_lines_locked() 覆盖。

        验证：① SU 序列不存在；② \\033[r 存在（重置滚动区域为全屏）；
        ③ 新 DECSTBM 正确设置。
        """
        self.bb._last_text = "A" * 500  # 长文本，_bottom_lines 会增大
        self.bb._last_bottom_lines = 3  # 旧底部行数较小
        self.bb._last_rendered_text = "old"

        output = self._capture_ansi_order(lambda: self.bb.force_redraw())

        import re
        # ★ 验证无 SU 序列
        su_match = re.search(r'\x1b\[(\d+)S', output)
        self.assertIsNone(su_match, "不应输出 SU 上滚序列")
        # ★ 验证 \\033[r 存在（重置滚动区域为全屏）
        self.assertIn("\033[r", output, "应输出 \\033[r 重置滚动区域")
        # ★ 验证新 DECSTBM 存在
        new_decstbm = re.search(r'\x1b\[\d+;\d+r', output)
        self.assertIsNotNone(new_decstbm, "应设置新 DECSTBM")

    def test_shrink_clears_reclaimed_area(self):
        """★ 2026-06-11 修复: force_redraw 在 delta < 0 时不再输出 SD 下滚序列。

        改为：直接清除回收区域行（不使用 SD 下滚），避免内容位移。
        回收区域空白将由新输出自然填充。
        验证：① SD 不存在；② \\033[r 存在；③ 新 DECSTBM 存在；
        ④ 清除操作发生在 DECSTBM 之后（回收区域 23-25，因 _last_height=30, old_bl=8, new_bl=5）。
        """
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 8  # 旧值较大
        self.bb._last_rendered_text = "old"

        output = self._capture_ansi_order(lambda: self.bb.force_redraw())

        import re
        # ★ 验证 SD 下滚序列不存在
        self.assertIsNone(re.search(r'\x1b\[\d+T', output),
                          "不应输出 SD 下滚序列")
        # ★ 验证 \033[r 仍然存在
        self.assertIn("\033[r", output,
                      "应输出 \\033[r 重置滚动区域")
        # ★ 验证新 DECSTBM 已设置: delta = 5-8 = -3, scroll_end = 30-5 = 25
        self.assertIn("\033[1;25r", output, "应设置新 DECSTBM [1;25r")
        # ★ 验证清除操作发生在 DECSTBM 之后（回收区域 23-25）
        decstbm_pos = output.index("\033[1;25r")
        for r in range(23, 26):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"应清除回收区域行 {r}")
            self.assertGreater(output.index(f"\033[{r};1H\033[K"), decstbm_pos,
                               f"行 {r} 的清除应在 DECSTBM 之后")

    @unittest.skip("_check_resize 已从 _BottomBar 移除")
    def test_shrink_path_uses_height(self):
        """_check_resize shrink 分支仍使用 height（新终端高度）定位（全屏滚动场景）。"""
        # setup: height=30, shrink to 25
        self.bb._last_bottom_lines = 5
        self.bb._setup_height = 30
        self.bb._last_text = "test"
        # 缩小后 shrink 路径使用新 height=25 定位到新终端末行
        from unittest.mock import patch as u_patch
        with u_patch("shutil.get_terminal_size",
                      return_value=(80, 25)), \
             u_patch("src.ui._bottom_bar._try_acquire_output_lock",
                     return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                            __exit__=MagicMock(return_value=False))):
            with patch.object(sys, '__stdout__', io.StringIO()) as buf:
                self.bb._check_resize()

        output = buf.getvalue()
        # shrink 路径中 _apply_scroll_delta(scroll_n, out, height) 使用新 height=25
        # 定位到终端末行：\033[25;1H（新终端高度）
        self.assertIn("\033[25;1H", output,
                      "shrink 路径应使用新 height(25) 定位光标到终端末行")


class TestApplyScrollDelta(unittest.TestCase):
    """验证 _apply_scroll_delta 在 delta 各取值时的 ANSI 输出。

    核心场景：
      1. delta > 0 → 输出 SU（上滚）序列
      2. delta <= 0 → 无操作（不输出 SD，避免删除上屏可见内容）
      3. old_scroll_end < 1 → 无操作
      4. hide_completions 触发 delta < 0 路径 → 不输出 SD 序列（回归测试）
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_height = 30  # 哨兵，force_redraw 需要
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_apply_scroll_delta_positive(self):
        """delta > 0 时输出 SU 上滚序列。"""
        buf = io.StringIO()
        self.bb._apply_scroll_delta(buf, delta=3, old_scroll_end=25)
        output = buf.getvalue()
        self.assertIn("\033[25;1H", output, "应定位到 old_scroll_end=25")
        self.assertIn("\033[3S", output, "delta=3 时应输出 SU 上滚 3 行")

    def test_apply_scroll_delta_negative(self):
        """delta < 0 时 _apply_scroll_delta 无操作（回收由 _reclaim_scroll_back 处理）。"""
        buf = io.StringIO()
        self.bb._apply_scroll_delta(buf, delta=-3, old_scroll_end=22)
        output = buf.getvalue()
        self.assertEqual(output, "", "delta=-3 时 _apply_scroll_delta 应无输出")

    def test_reclaim_scroll_back_negative(self):
        """delta < 0 时 _reclaim_scroll_back 输出 SD 序列并清除顶部空行。"""
        buf = io.StringIO()
        self.bb._reclaim_scroll_back(buf, delta=-3, scroll_end=25)
        output = buf.getvalue()
        self.assertIn("\033[25;1H", output, "应定位到 scroll_end=25")
        self.assertIn("\033[3T", output, "delta=-3 时应输出 SD 下滚 3 行")
        # 必须清除 SD 产生的顶部空行
        for r in range(1, 4):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"SD 后应清除顶部行 {r}")

    def test_reclaim_scroll_back_non_negative(self):
        """delta >= 0 时 _reclaim_scroll_back 无操作。"""
        buf = io.StringIO()
        self.bb._reclaim_scroll_back(buf, delta=0, scroll_end=25)
        self.assertEqual(buf.getvalue(), "", "delta=0 时应无输出")
        buf2 = io.StringIO()
        self.bb._reclaim_scroll_back(buf2, delta=3, scroll_end=25)
        self.assertEqual(buf2.getvalue(), "", "delta>0 时应无输出")

    def test_apply_scroll_delta_zero(self):
        """delta == 0 时无操作。"""
        buf = io.StringIO()
        self.bb._apply_scroll_delta(buf, delta=0, old_scroll_end=25)
        output = buf.getvalue()
        self.assertEqual(output, "", "delta=0 时应无 ANSI 输出")

    def test_apply_scroll_delta_scroll_end_zero(self):
        """old_scroll_end=0 时无操作。"""
        buf = io.StringIO()
        self.bb._apply_scroll_delta(buf, delta=3, old_scroll_end=0)
        output = buf.getvalue()
        self.assertEqual(output, "", "old_scroll_end=0 时应无操作")

    def test_apply_scroll_delta_scroll_end_negative(self):
        """old_scroll_end=-1 时无操作。"""
        buf = io.StringIO()
        self.bb._apply_scroll_delta(buf, delta=-3, old_scroll_end=-1)
        output = buf.getvalue()
        self.assertEqual(output, "", "old_scroll_end=-1 时应无操作")

    def test_hide_completions_scroll_down(self):
        """★ 2026-06-28: hide_completions() 触发 delta < 0，仅清除释放区域，不调用 save/restore。"""
        # 模拟补全弹窗已弹出（底部栏扩大）的状态
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 4  # 弹窗占 4 行
        self.bb._completion._title = "补全"
        self.bb._completion._items = ["item1", "item2"]
        self.bb._completion._texts = ["item1", "item2"]
        self.bb._completion._idx = 0
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 9  # 旧底部栏：2 + 3 + 4 = 9
        self.bb._last_rendered_text = "test"
        self.bb._last_status = ""

        buf = io.StringIO()
        # ★ mock _term_height/_term_width（而非 shutil.get_terminal_size），
        #    因为 force_redraw() 优先使用 Blessed Terminal
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.hide_completions()

        output = buf.getvalue()
        # old_scroll_end = 30 - 9 = 21
        # delta = (2 + 3 + 0) - 9 = 5 - 9 = -4
        # scroll_end = 30 - 5 = 25
        self.assertIn("\033[1;25r", output, "应设置新 DECSTBM [1;25r")
        self.assertNotIn("\033[4T", output,
                         "hide 不应输出 SD 下滚")
        # ★ 验证 delta<0 释放区域被清空（行 22-25，即 old_scroll_end+1 到 scroll_end）
        for r in range(22, 26):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"hide 应清除 delta<0 释放区域行 {r}")
        # 清除顶部行不应出现（非 SD 路径）
        for r in range(1, 5):
            self.assertNotIn(f"\033[{r};1H\033[K", output,
                             f"hide 不应清除 SD 产生的顶部行 {r}")

    def test_force_redraw_shrink_no_sd(self):
        """★ 2026-06-11 修复: force_redraw 在 delta < 0 时不再输出 SD 下滚序列。

        改为：直接清除回收区域行，避免内容位移。
        验证：① SD 不存在；② 新 DECSTBM 存在；③ 清除操作确实发生。
        """
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 8  # 旧值（较大）
        self.bb._last_rendered_text = "old"
        self.bb._last_status = ""

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        output = buf.getvalue()
        import re
        # ★ 验证 SD 下滚序列不存在
        self.assertIsNone(re.search(r'\x1b\[\d+T', output),
                          "不应输出 SD 下滚序列")
        # ★ 验证新 DECSTBM 已设置: delta = 5-8 = -3, scroll_end = 30-5 = 25
        self.assertIn("\033[1;25r", output, "应设置新 DECSTBM [1;25r")
        # ★ 验证清除操作发生在 DECSTBM 之后（回收区域 23-25）
        decstbm_pos = output.index("\033[1;25r")
        for r in range(23, 26):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"应清除回收区域行 {r}")
            self.assertGreater(output.index(f"\033[{r};1H\033[K"), decstbm_pos,
                               f"行 {r} 的清除应在 DECSTBM 之后")

    def test_show_completions_then_hide_no_blank_lines(self):
        """★ 2026-06-28: hide 时清空释放区域，不恢复旧内容（save/restore 已移除）。"""
        # ── Step 1: 模拟 show_completions ──
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 5  # 初始底部栏 5 行
        self.bb._last_rendered_text = "test"
        self.bb._last_status = ""
        items = ["item_a", "item_b", "item_c"]

        buf_show = io.StringIO()
        # ★ mock _term_height/_term_width（而非 shutil.get_terminal_size），
        #    因为 force_redraw() 优先使用 Blessed Terminal
        with patch.object(sys, '__stdout__', buf_show), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.show_completions(items, selected_idx=0, title="补全")

        show_output = buf_show.getvalue()
        # show 设置 DECSTBM 并重绘底部栏，不输出 SU（SU 已从 force_redraw 移除）
        self.assertNotEqual(show_output, "", "show_completions 应触发 force_redraw")

        # ── Step 2: 模拟 hide_completions ──
        buf_hide = io.StringIO()
        with patch.object(sys, '__stdout__', buf_hide), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.hide_completions()

        hide_output = buf_hide.getvalue()
        self.assertNotIn("\033[5T", hide_output,
                         "hide 不应输出 SD 下滚")
        self.assertNotIn("\033[5S", hide_output,
                         "hide 不应输出 SU 上滚")
        # ★ 验证释放区域被清空而非恢复旧内容
        self.assertIn("\033[r", hide_output,
                      "hide 应重置滚动区域为全屏")
        # ★ 验证 delta<0 释放区域被清空（行 22-25，即 old_scroll_end+1 到 scroll_end）
        # old_scroll_end = 30 - 9 = 21, scroll_end = 30 - 5 = 25
        for r in range(22, 26):
            self.assertIn(f"\033[{r};1H\033[K", hide_output,
                          f"hide 应清除 delta<0 释放区域行 {r}")
        # 不应有 SD 产生的顶部清除
        for r in range(1, 6):
            self.assertNotIn(f"\033[{r};1H\033[K", hide_output,
                             f"hide 后不应清除 SD 产生的顶部行 {r}")


class TestStdoutLineTracker(unittest.TestCase):
    """测试 _StdoutLineTracker 的独立功能。

    核心场景：
      1. 穿透写入 — 所有 write/flush 原封不动传到真实 stdout
      2. 行检测与环形缓冲区 — \\n 将内容按行拆分存入 deque
      3. 底部栏内容过滤 — 光标定位到 r > scroll_end 时跳过追踪
      4. 容量限制 — deque maxlen=300
      5. scroll_end=0 跳过追踪
      6. 文件协议属性 — encoding, errors, buffer, fileno, isatty, writable
    """

    def setUp(self):
        self._real_stdout = sys.__stdout__
        self._buf = io.StringIO()

    def tearDown(self):
        pass  # We don't modify sys.__stdout__ in these tests

    def _make_tracker(self, scroll_end: int = 20) -> _StdoutLineTracker:
        """Create a tracker wrapping a StringIO buffer."""
        tracker = _StdoutLineTracker(self._buf)
        tracker.set_scroll_end(scroll_end)
        return tracker

    # ── 1. 穿透写入 ──

    def test_write_pass_through(self):
        """写入内容应穿透到真实 stdout。"""
        tracker = self._make_tracker()
        tracker.write("hello world\n")
        self.assertIn("hello world\n", self._buf.getvalue())

    def test_flush_pass_through(self):
        """flush 应穿透到真实 stdout（不抛异常）。"""
        tracker = self._make_tracker()
        tracker.flush()  # Should not raise

    # ── 2. 行检测与环形缓冲区 ──

    def test_line_detection_basic(self):
        """\\n 将内容按行拆分存入环形缓冲区。"""
        tracker = self._make_tracker()
        tracker.write("line1\nline2\nline3\n")
        ring_lines = list(tracker._ring)
        self.assertEqual(len(ring_lines), 3)
        self.assertEqual(ring_lines, ["line1", "line2", "line3"])

    def test_line_detection_with_ansi(self):
        """包含 ANSI 码的行应原样存入环形缓冲区。"""
        tracker = self._make_tracker()
        tracker.write("\033[32mgreen text\033[0m\n")
        tracker.write("normal text\n")
        ring_lines = list(tracker._ring)
        self.assertEqual(len(ring_lines), 2)
        self.assertIn("\033[32mgreen text\033[0m", ring_lines[0])

    def test_partial_line_handling(self):
        """不完整的行（无 \\n）暂存，\\n 到来时才提交。"""
        tracker = self._make_tracker()
        tracker.write("start ")
        tracker.write("middle ")
        tracker.write("end\n")
        ring_lines = list(tracker._ring)
        self.assertEqual(len(ring_lines), 1)
        self.assertEqual(ring_lines[0], "start middle end")

    # ── 3. 底部栏内容过滤 ──

    def test_bottom_bar_content_filtered(self):
        """光标定位到 r > scroll_end 时内容不被追踪。"""
        tracker = self._make_tracker(scroll_end=20)
        tracker.write("visible line\n")
        # 定位到 row 21 (> scroll_end 20) → 底部栏模式
        tracker.write("\033[21;1Hbottom bar content")
        # 恢复定位到 row 20
        tracker.write("\033[20;1Hsecond visible line\n")
        ring_lines = list(tracker._ring)
        self.assertIn("visible line", ring_lines[0])
        self.assertIn("second visible line", ring_lines[1])
        # 底部栏内容不应出现在缓冲区
        all_text = "\n".join(ring_lines)
        self.assertNotIn("bottom bar content", all_text)

    def test_bottom_bar_cursor_restore_exits_mode(self):
        """\\0338（光标恢复）应退出底部栏模式。"""
        tracker = self._make_tracker(scroll_end=20)
        tracker.write("\033[21;1H")  # Enter bottom bar
        tracker.write("bottom stuff")
        tracker.write("\0338")       # Restore cursor → exit bottom bar
        tracker.write("visible again\n")
        ring_lines = list(tracker._ring)
        self.assertIn("visible again", ring_lines[0])

    def test_cursor_position_to_scroll_area_exits_bottom_bar(self):
        """定位到 r <= scroll_end 应退出底部栏模式。"""
        tracker = self._make_tracker(scroll_end=20)
        tracker.write("\033[25;1H")  # Enter bottom bar (25 > 20)
        tracker.write("bottom stuff")
        tracker.write("\033[20;1H")  # Back to scroll area
        tracker.write("scroll content\n")
        ring_lines = list(tracker._ring)
        self.assertIn("scroll content", ring_lines[0])

    # ── 5. 容量限制 ──

    def test_ring_buffer_max_lines(self):
        """环形缓冲区不超过 300 行。"""
        tracker = self._make_tracker()
        for i in range(500):
            tracker.write(f"line_{i}\n")
        ring_lines = list(tracker._ring)
        self.assertLessEqual(len(ring_lines), 300)

    # ── 6. scroll_end=0 跳过追踪 ──

    def test_scroll_end_zero_skips_tracking(self):
        """scroll_end < 1 时完全跳过行追踪。"""
        tracker = _StdoutLineTracker(self._buf)  # scroll_end=0 by default
        tracker.write("line1\nline2\n")
        ring_lines = list(tracker._ring)
        self.assertEqual(len(ring_lines), 0, "scroll_end=0 时不应追踪任何行")

    # ── 7. 文件协议属性 ──

    def test_file_protocol_encoding(self):
        tracker = _StdoutLineTracker(self._real_stdout)
        self.assertEqual(tracker.encoding, 'utf-8')

    def test_file_protocol_errors(self):
        tracker = _StdoutLineTracker(self._real_stdout)
        self.assertEqual(tracker.errors, 'surrogateescape')

    def test_file_protocol_isatty(self):
        tracker = _StdoutLineTracker(self._real_stdout)
        self.assertEqual(tracker.isatty(), self._real_stdout.isatty())

    def test_file_protocol_writable(self):
        tracker = self._make_tracker()
        self.assertTrue(tracker.writable())

    def test_write_returns_length(self):
        """write() 返回写入的字符数（len(data)）。"""
        tracker = self._make_tracker()
        result = tracker.write("hello")
        self.assertEqual(result, 5)


class TestCompletionShowHideWithTracker(unittest.TestCase):
    """补全弹窗状态设置测试（I/O 已迁移至 render 线程）。

    2026-06-11 重构：show_completions/hide_completions 剥离终端 I/O，
    仅设置 _completion 状态。SU/SD/DECSTBM 等 ANSI 操作由 render 线程
    在 force_redraw() 中统一执行。以下测试验证状态设置的正确性。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 5
        self.bb._last_rendered_text = "test"
        self.bb._last_status = ""
        self.bb._last_height = 30
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_show_completions_sets_state(self):
        """show_completions 设置 _completion 状态并触发 force_redraw。"""
        items = ["item_a", "item_b", "item_c", "item_d", "item_e"]

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.show_completions(items, selected_idx=0, title="补全")

        output = buf.getvalue()
        self.assertNotEqual(output, "", "show_completions 应触发 force_redraw 终端 I/O")
        self.assertTrue(self.bb.is_completion_visible, "弹窗应可见")
        self.assertEqual(self.bb._completion._title, "补全")
        self.assertEqual(self.bb._completion._idx, 0)
        self.assertEqual(len(self.bb._completion._items), 5)

    def test_hide_completions_clears_state(self):
        """hide_completions 清除 _completion 状态并触发 force_redraw。"""
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 4
        self.bb._completion._title = "补全"
        self.bb._completion._items = ["item1", "item2"]
        self.bb._completion._texts = ["item1", "item2"]
        self.bb._completion._idx = 1
        self.bb._last_bottom_lines = 9

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.hide_completions()

        output = buf.getvalue()
        self.assertNotEqual(output, "", "hide_completions 应触发 force_redraw 终端 I/O")
        self.assertFalse(self.bb.is_completion_visible, "弹窗应不可见")
        self.assertEqual(self.bb._completion._popup_height, 0)
        self.assertEqual(self.bb._completion._idx, 0)

    def test_hide_completions_idempotent(self):
        """hide_completions 幂等：重复调用无效果（弹窗未显示时不触发 force_redraw）。"""
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf):
            self.bb.hide_completions()

        output = buf.getvalue()
        self.assertEqual(output, "", "幂等调用不应有终端 I/O（弹窗未显示）")

if __name__ == "__main__":
    unittest.main()
