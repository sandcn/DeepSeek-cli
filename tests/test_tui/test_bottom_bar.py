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


class TestBottomBarClearOldBottom(unittest.TestCase):
    """验证 clear_old_bottom() 清除旧底部栏区域。

    核心场景：
      1. clear_old_bottom() 非活跃时跳过
      2. clear_old_bottom() 清除旧底部栏行并定位光标
    """

    def setUp(self):
        self.bb = _BottomBar()
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_clear_old_bottom_inactive(self):
        """非活跃时 clear_old_bottom() 不输出任何序列。"""
        self.bb._active = False
        out = io.StringIO()
        with patch.object(sys, '__stdout__', out):
            self.bb.clear_old_bottom()
        self.assertEqual(out.getvalue(), "")

    def test_clear_old_bottom_active(self):
        """活跃时 clear_old_bottom() 清除 last_height 区域内行。"""
        self.bb._active = True
        self.bb._last_height = 30
        self.bb._last_bottom_lines = 5  # 旧底部栏 5 行

        mock_term = _mock_terminal(width=80, height=30)
        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch("src.ui._bottom_bar.get_terminal", return_value=mock_term):
            self.bb.clear_old_bottom()

        output = out.getvalue()
        # 旧底部栏起始行: 30 - 5 + 1 = 26
        # 应清除行 26-30
        for r in range(26, 31):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"应清除行 {r}")
        # 光标应定位到 26 行（清除区域起始行）
        self.assertIn("\033[26;1H", output)


class TestDrainQueueClearOldBottom(unittest.TestCase):
    """验证 _drain_queue() Stage 1 调用 clear_old_bottom()。"""

    def setUp(self):
        from src.chat_ui._engine import RenderEngine
        self.mock_renderer = MagicMock()
        self.mock_bb = MagicMock()
        self.mock_bb.is_status_active = False
        self.mock_bb._active = True
        self.mock_bb._last_height = 30
        self.mock_bb._last_bottom_lines = 5
        self.mock_bb.get_cursor_info.return_value = ("", 0, 24, 80)
        self.engine = RenderEngine(self.mock_renderer, self.mock_bb)
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def _enqueue_cmd(self):
        from src.chat_ui._const import RenderCommand
        self.engine.push_cmd((RenderCommand.NOTIFICATION, "test"))

    def test_phase_render_calls_clear_old_bottom(self):
        """_phase_render 应先调用 clear_old_bottom 再渲染命令。"""
        self._enqueue_cmd()

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        # 验证 clear_old_bottom 被调用过
        self.mock_bb.clear_old_bottom.assert_called()
        # 验证渲染器被调用
        self.mock_renderer.render.assert_called()

    def test_clear_old_bottom_exception_tolerated(self):
        """clear_old_bottom 异常时应继续渲染命令。"""
        self._enqueue_cmd()
        self.mock_bb.clear_old_bottom.side_effect = RuntimeError("boom")

        with \
             patch("src.ui._lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        # 渲染器仍被调用（异常被吞掉）
        self.mock_renderer.render.assert_called()


class TestForceRedrawNoDECSTBM(unittest.TestCase):
    """验证 force_redraw 不输出 DECSTBM/SU/SD 序列。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 5
        self.bb._last_rendered_text = "test"
        self.bb._last_height = 30
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_force_redraw_no_decstbm(self):
        """force_redraw 输出不应包含 DECSTBM 序列。"""
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)):
            self.bb.force_redraw()

        output = buf.getvalue()
        # 不应包含 DECSTBM 设置序列
        self.assertNotIn("\033[1;25r", output, "不应包含 DECSTBM 设置序列")
        # 不应包含 DECSTBM 重置序列
        self.assertNotIn("\033[r", output, "不应包含 DECSTBM 重置序列")

    def test_force_redraw_no_su_sd(self):
        """force_redraw 输出不应包含 SU/SD 序列。"""
        self.bb._last_text = "A" * 500  # 长文本
        self.bb._last_bottom_lines = 3  # 旧值较小，触发扩展
        self.bb._last_rendered_text = "old"

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)):
            self.bb.force_redraw()

        output = buf.getvalue()
        import re
        self.assertFalse(re.search(r'\x1b\[\d+S', output),
                         "不应包含 SU 上滚序列")
        self.assertFalse(re.search(r'\x1b\[\d+T', output),
                         "不应包含 SD 下滚序列")


class TestShowHideCompletionsNoDECSTBM(unittest.TestCase):
    """验证 show/hide_completions 不输出 DECSTBM/SU/SD 序列。"""

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 5
        self.bb._last_rendered_text = "test"
        self.bb._last_height = 30
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_show_completions_no_decstbm(self):
        """show_completions 输出不应包含 DECSTBM 序列。"""
        self.bb._last_bottom_lines = 5
        self.bb._last_rendered_text = "test"
        self.bb._last_height = 30

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)):
            self.bb.show_completions(["item1", "item2"], 0)

        output = buf.getvalue()
        self.assertNotIn("\033[1;2", output, "不应包含 DECSTBM 设置序列")
        self.assertNotIn("\033[r", output, "不应包含 DECSTBM 重置序列")

    def test_hide_completions_no_decstbm(self):
        """hide_completions 输出不应包含 DECSTBM 序列。"""
        # 先设置补全状态
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 4
        self.bb._completion._items = ["item1", "item2"]
        self.bb._completion._texts = ["item1", "item2"]
        self.bb._completion._idx = 0
        self.bb._last_bottom_lines = 9  # 旧底部栏较大
        self.bb._last_height = 30

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.hide_completions()

        output = buf.getvalue()
        self.assertNotIn("\033[1;2", output, "不应包含 DECSTBM 设置序列")
        self.assertNotIn("\033[r", output, "不应包含 DECSTBM 重置序列")

    def test_force_redraw_shrink_outputs_sd(self):
        """force_redraw 不应输出 DECSTBM 或 SD 序列（无 DECSTBM 模式）。"""
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 8  # 旧值（较大）
        self.bb._last_rendered_text = "old"
        self.bb._last_status = ""

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        output = buf.getvalue()
        self.assertNotIn("\033[1;25r", output, "不应包含 DECSTBM 序列")
        self.assertNotIn("\033[3T", output, "不应包含 SD 序列")
        self.assertNotIn("\033[r", output, "不应包含 DECSTBM 重置")

    def test_show_completions_then_hide_no_blank_lines(self):
        """集成测试：show/hide 不再使用 SU/SD/tracker（无 DECSTBM 模式）。"""
        self.bb._last_text = "test"
        self.bb._last_bottom_lines = 5
        self.bb._last_rendered_text = "test"
        self.bb._last_status = ""
        items = ["item_a", "item_b", "item_c"]

        buf_show = io.StringIO()
        with patch.object(sys, '__stdout__', buf_show), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.show_completions(items, selected_idx=0, title="补全")

        show_output = buf_show.getvalue()
        self.assertNotIn("\033[5S", show_output, "不应包含 SU 序列")
        self.assertNotIn("\033[r", show_output, "不应包含 DECSTBM 重置")
        # 弹窗内容应出现
        for item in items:
            self.assertIn(item, show_output, f"应包含弹窗项: {item}")

        # Step 2: hide
        buf_hide = io.StringIO()
        with patch.object(sys, '__stdout__', buf_hide), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.hide_completions()

        hide_output = buf_hide.getvalue()
        self.assertNotIn("\033[5T", hide_output, "不应包含 SD 序列")
        self.assertNotIn("\033[5S", hide_output, "不应包含 SU 序列")
        self.assertNotIn("\033[r", hide_output, "不应包含 DECSTBM 重置")


class TestStdoutLineTracker(unittest.TestCase):
    """测试 _StdoutLineTracker 的独立功能。

    核心场景：
      1. 穿透写入 — 所有 write/flush 原封不动传到真实 stdout
      2. 行检测与环形缓冲区 — \\n 将内容按行拆分存入 deque
      3. 底部栏内容过滤 — 光标定位到 r > scroll_end 时跳过追踪
      4. 保存/恢复 — save_rows_to_restore / get_saved_rows / clear_saved
      5. 容量限制 — deque maxlen=300
      6. scroll_end=0 跳过追踪
      7. 文件协议属性 — encoding, errors, buffer, fileno, isatty, writable
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
        """\\n 将内容按行拆分存入缓冲区。"""
        tracker = self._make_tracker()
        tracker.write("line1\nline2\nline3\n")
        saved = tracker.get_saved_rows()
        self.assertIsNone(saved, "未调用 save_rows_to_restore 时应为 None")

    def test_line_detection_with_ansi(self):
        """包含 ANSI 码的行应原样存入缓冲区。"""
        tracker = self._make_tracker()
        tracker.write("\033[32mgreen text\033[0m\n")
        tracker.write("normal text\n")
        tracker.save_rows_to_restore(2)
        saved = tracker.get_saved_rows()
        self.assertEqual(len(saved), 2)
        self.assertIn("\033[32mgreen text\033[0m", saved[0])

    def test_partial_line_handling(self):
        """不完整的行（无 \\n）暂存，\\n 到来时才提交。"""
        tracker = self._make_tracker()
        tracker.write("start ")
        tracker.write("middle ")
        tracker.write("end\n")
        tracker.save_rows_to_restore(1)
        saved = tracker.get_saved_rows()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0], "start middle end")

    def test_save_rows_to_restore_partial(self):
        """请求保存的行数超过缓冲区时返回全部。"""
        tracker = self._make_tracker()
        tracker.write("a\nb\n")
        tracker.save_rows_to_restore(10)
        saved = tracker.get_saved_rows()
        self.assertEqual(len(saved), 2)

    # ── 3. 底部栏内容过滤 ──

    def test_bottom_bar_content_filtered(self):
        """光标定位到 r > scroll_end 时内容不被追踪。"""
        tracker = self._make_tracker(scroll_end=20)
        tracker.write("visible line\n")
        # 定位到 row 21 (> scroll_end 20) → 底部栏模式
        tracker.write("\033[21;1Hbottom bar content")
        # 恢复定位到 row 20
        tracker.write("\033[20;1Hsecond visible line\n")
        tracker.save_rows_to_restore(10)
        saved = tracker.get_saved_rows()
        self.assertIn("visible line", saved[0])
        self.assertIn("second visible line", saved[1])
        # 底部栏内容不应出现在缓冲区
        all_text = "\n".join(saved)
        self.assertNotIn("bottom bar content", all_text)

    def test_bottom_bar_cursor_restore_exits_mode(self):
        """\\0338（光标恢复）应退出底部栏模式。"""
        tracker = self._make_tracker(scroll_end=20)
        tracker.write("\033[21;1H")  # Enter bottom bar
        tracker.write("bottom stuff")
        tracker.write("\0338")       # Restore cursor → exit bottom bar
        tracker.write("visible again\n")
        tracker.save_rows_to_restore(1)
        saved = tracker.get_saved_rows()
        self.assertIn("visible again", saved[0])

    def test_cursor_position_to_scroll_area_exits_bottom_bar(self):
        """定位到 r <= scroll_end 应退出底部栏模式。"""
        tracker = self._make_tracker(scroll_end=20)
        tracker.write("\033[25;1H")  # Enter bottom bar (25 > 20)
        tracker.write("bottom stuff")
        tracker.write("\033[20;1H")  # Back to scroll area
        tracker.write("scroll content\n")
        tracker.save_rows_to_restore(1)
        saved = tracker.get_saved_rows()
        self.assertIn("scroll content", saved[0])

    # ── 4. 保存/恢复 ──

    def test_save_and_get_rows(self):
        """save_rows_to_restore 保存最后 n 行，get_saved_rows 返回它们。"""
        tracker = self._make_tracker()
        for i in range(5):
            tracker.write(f"line_{i}\n")
        tracker.save_rows_to_restore(3)
        saved = tracker.get_saved_rows()
        self.assertEqual(len(saved), 3)
        self.assertEqual(saved, ["line_2", "line_3", "line_4"])

    def test_clear_saved(self):
        """clear_saved 后 get_saved_rows 返回 None。"""
        tracker = self._make_tracker()
        tracker.write("test\n")
        tracker.save_rows_to_restore(1)
        self.assertIsNotNone(tracker.get_saved_rows())
        tracker.clear_saved()
        self.assertIsNone(tracker.get_saved_rows())

    def test_save_zero_rows_does_nothing(self):
        """save_rows_to_restore(0) 不保存任何内容。"""
        tracker = self._make_tracker()
        tracker.write("test\n")
        tracker.save_rows_to_restore(0)
        self.assertIsNone(tracker.get_saved_rows())

    # ── 5. 容量限制 ──

    def test_ring_buffer_max_lines(self):
        """环形缓冲区不超过 300 行。"""
        tracker = self._make_tracker()
        for i in range(500):
            tracker.write(f"line_{i}\n")
        tracker.save_rows_to_restore(500)
        saved = tracker.get_saved_rows()
        self.assertLessEqual(len(saved), 300)

    # ── 6. scroll_end=0 跳过追踪 ──

    def test_scroll_end_zero_skips_tracking(self):
        """scroll_end < 1 时完全跳过行追踪。"""
        tracker = _StdoutLineTracker(self._buf)  # scroll_end=0 by default
        tracker.write("line1\nline2\n")
        tracker.save_rows_to_restore(10)
        saved = tracker.get_saved_rows()
        self.assertIsNone(saved, "scroll_end=0 时 save 应无效果")

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


class TestCompletionShowHideNoTracker(unittest.TestCase):
    """集成测试：show/hide 不再使用 tracker（无 DECSTBM 模式）。

    核心场景：
      1. show → 清除旧底部栏并绘制含弹窗的新底部栏
      2. hide → 清除旧底部栏并绘制无弹窗的新底部栏
      3. hide → 输出不包含 SD/DECSTBM 序列
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

    def test_show_completions_no_decstbm(self):
        """show_completions 应正确绘制弹窗而不输出 DECSTBM 序列。"""
        items = ["item_a", "item_b", "item_c", "item_d", "item_e"]

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.show_completions(items, selected_idx=0, title="补全")

        output = buf.getvalue()
        # 不应包含 DECSTBM/SD 序列
        import re
        self.assertFalse(re.search(r'\x1b\[\d+;\d+r', output),
                         "不应包含 DECSTBM 序列")
        self.assertFalse(re.search(r'\x1b\[\d+[ST]', output),
                         "不应包含 SU/SD 序列")
        # 应包含弹窗内容
        for item in items:
            self.assertIn(item, output, f"应包含弹窗项: {item}")

    def test_hide_completions_no_sd(self):
        """hide_completions 不应输出 SD 序列。"""
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 4
        self.bb._completion._title = "补全"
        self.bb._completion._items = ["item1", "item2"]
        self.bb._completion._texts = ["item1", "item2"]
        self.bb._last_bottom_lines = 9

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("shutil.get_terminal_size", return_value=(80, 30)), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.hide_completions()

        output = buf.getvalue()
        self.assertNotIn("\033[4T", output,
                         "hide 不应输出 SD 下滚")
        self.assertNotIn("\033[r", output,
                         "hide 不应输出 DECSTBM 重置")


if __name__ == "__main__":
    unittest.main()
