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

from src.tui.widgets.bottom_bar import _BottomBar
from src.tui.widgets.stdout_tracker import _StdoutLineTracker


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
        self.bb._last_status = "test-model ·"
        # 禁用终端 I/O
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    # ── 场景 1：纯光标移动（文本不变，状态活跃） ──────────

    def test_cursor_move_during_streaming(self):
        """流式输出期间设置光标位置 → _input_cursor_pos 应正确更新。"""
        # 模拟用户按 ← 将光标从末尾(11)移到 "world" 的 'w'(6)
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model ·  5t"):
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
             patch.object(self.bb, '_format_status', return_value="test-model · 10t"):
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
             patch.object(self.bb, '_format_status', return_value="test-model · 10t"):
            self.bb._last_text = "hello world"
            self.bb.force_redraw()  # 不改变 _input_cursor_pos

        self.assertEqual(self.bb._input_cursor_pos, 5,
                         "不设置光标位时应保持原值")

    # ── 场景 4：文本变化 + 光标移动 ────────────────────

    def test_text_change_with_cursor_pos(self):
        """文本变化 + 设置光标位置 → _input_cursor_pos 应更新。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model · 10t"):
            self.bb._last_text = "hello world!"
            self.bb._input_cursor_pos = 12
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 12,
                         "文本变化时光标应更新到新位置")

    # ── 场景 5：文本变化，光标在末尾 ─────────────────

    def test_text_change_cursor_at_end(self):
        """文本变化，光标在末尾 → _input_cursor_pos 应为文本长度。"""
        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch.object(self.bb, '_format_status', return_value="test-model · 10t"):
            self.bb._last_text = "hello world!"
            self.bb._input_cursor_pos = 12  # 即 len("hello world!")
            self.bb.force_redraw()

        self.assertEqual(self.bb._input_cursor_pos, 12,
                         "文本变化+末尾光标应正确保持")

    # ── 场景 6：force_redraw 始终全量重绘 ─────

    def test_force_redraw_always_full_redraw(self):
        """布局和状态均未变时 force_redraw 仍应全量重绘（10Hz 重构后无快速路径）。"""
        self.bb._last_status = "test-model ·"
        self.bb._last_rendered_text = "hello world"
        self.bb._last_text = "hello world"
        self.bb._last_bottom_lines = self.bb._bottom_lines
        self.bb._last_height = self.bb._term_height()  # 需同步终端高度

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch.object(self.bb, '_format_status', return_value="test-model ·"):
            self.bb.force_redraw()

        output = out.getvalue()
        # 即使布局和状态未变，也应输出全量重绘 ANSI 序列
        self.assertNotEqual(output, "",
                            "布局和状态未变时 force_redraw 仍应输出全量重绘内容")
        # 应包含全量重绘特有内容：分隔线、DECSTBM
        self.assertIn("\u2501", output, "应包含分隔线字符 (━)")
        self.assertIn("\033[1;", output, "应包含 DECSTBM 设置序列")

    def test_force_redraw_full_redraw_when_status_changes(self):
        """★ 10Hz 重构后：布局不变仅状态变化时，仍执行全量重绘（不再有增量路径）。

        验证 force_redraw() 始终走全量重绘路径：
        - 即使 layout_unchanged=True 且 new_status != _last_status
        - 也输出分隔线、DECSTBM、输入行等全量重绘内容
        """
        # 设置 layout_unchanged=True 的条件
        self.bb._last_rendered_text = "hello world"
        self.bb._last_text = "hello world"
        self.bb._last_bottom_lines = self.bb._bottom_lines
        self.bb._last_height = self.bb._term_height()
        self.bb._last_subagent_lines = []
        self.bb._subagent_lines = []
        self.bb._last_status = "test-model ·"

        new_status = "test-model ·  10t"

        out = io.StringIO()
        with patch.object(sys, '__stdout__', out), \
             patch.object(self.bb, '_format_status', return_value=new_status):
            self.bb.force_redraw()

        output = out.getvalue()

        # 应有输出（始终全量重绘）
        self.assertNotEqual(output, "",
                            "状态变化时应产生全量重绘输出")

        # 应包含全量重绘特有内容
        self.assertIn("\u2501", output, "应包含分隔线字符 (━)")
        self.assertIn("\033[1;", output, "应包含 DECSTBM 设置序列")
        self.assertIn("\033[r", output, "应包含 DECSTBM 重置序列")

        # 应包含新状态文本
        self.assertIn(new_status, output, f"应包含新状态文本: {new_status}")

        # 验证重绘后状态正确更新
        self.assertEqual(self.bb._last_status, new_status,
                         "全量重绘后 _last_status 应更新为新状态")
        self.assertEqual(self.bb._last_cursor_pos, self.bb._input_cursor_pos,
                         "全量重绘后 _last_cursor_pos 应与 _input_cursor_pos 同步")


class TestComputeCursorPosition(unittest.TestCase):
    """验证 compute_cursor_position() 和 _compute_bottom_lines_for() 的数据源一致性修复。

    核心场景：
      1. _compute_bottom_lines_for — 空文本→最小底部行数（5）
      2. _compute_bottom_lines_for — 单行短文本→最小底部行数
      3. _compute_bottom_lines_for — 多行长文本→底部行数正确扩展
      4. compute_cursor_position — text 与 _last_text 不同时 total_bottom 基于 text
      5. _compute_bottom_lines_for — 补全弹窗可见时包含弹窗高度
      6. _compute_bottom_lines_for — 纯计算不产生副作用（幂等性）
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._subagent_lines = []
        # 补全弹窗初始不可见
        self.bb._completion._visible = False
        self.bb._completion._popup_height = 0

    # ── 场景 1：空文本→最小底部行数 ──────────────────────

    def test_compute_bottom_lines_for_empty_text(self):
        """空文本应返回最小底部行数 = 4 + 0 + _MIN_INPUT_ROWS(3) + 0 = 7。"""
        result = self.bb._compute_bottom_lines_for("", 80)
        self.assertEqual(result, 5,
                         "空文本应返回最小底部行数 5（_MIN_INPUT_ROWS=1，+2 顶底分割线）")

    # ── 场景 2：单行短文本→最小底部行数 ──────────────────

    def test_compute_bottom_lines_for_short_text(self):
        """单行短文本（不触发拆行）→ 底部行数仍为最小值 7。"""
        result = self.bb._compute_bottom_lines_for("hello", 80)
        # max_input = 76, expanded = "hello", wrapped = ["hello"], base = max(1, 1) = 1
        # return 4 + 0 + 1 + 0 = 5
        self.assertEqual(result, 5,
                         "单行短文本不触发拆行时应返回 5（_MIN_INPUT_ROWS=1）")

    # ── 场景 3：多行长文本→底部行数正确扩展 ─────────────

    def test_compute_bottom_lines_for_long_text(self):
        """多行长文本触发拆行 → 底部行数应正确扩展。"""
        # 500 个 'A' 在宽度 80（max_input=76）下拆为多行
        long_text = "A" * 500
        result = self.bb._compute_bottom_lines_for(long_text, 80)
        # 500 'A' 在 max_input=76 下：ceil(500/76) = 7 行
        # base = max(3, 7) = 7, return 2 + 0 + 7 + 0 = 9
        expected_lines = (500 + 75) // 76  # ceil(500/76)
        expected_base = max(3, expected_lines)
        expected = 4 + expected_base
        self.assertEqual(result, expected,
                         f"长文本底部行数应为 {expected}（+2 新增顶底分割线）")

        # 在更窄终端宽度下应有更多行
        result_narrow = self.bb._compute_bottom_lines_for(long_text, 40)
        # max_input = 36，500/36 ≈ 13.89 → 14 行
        narrow_lines = (500 + 35) // 36
        narrow_base = max(3, narrow_lines)
        narrow_expected = 4 + narrow_base
        self.assertEqual(result_narrow, narrow_expected,
                         f"窄终端下底部行数应为 {narrow_expected}")
        self.assertGreater(result_narrow, result,
                           "窄终端宽度应产生更多拆行")

    # ── 场景 4：text 与 _last_text 不同时 total_bottom 基于 text ─

    def test_compute_cursor_position_uses_text_not_last_text(self):
        """核心场景：text 参数与 _last_text 不同时，
        compute_cursor_position 的 total_bottom 应基于 text 而非 _last_text。"""
        # 模拟：_last_text（EscapeMonitor 线程最新值）比 text（渲染快照）长很多
        self.bb._last_text = "A" * 500  # EscapeMonitor 已更新为长文本
        text_snapshot = "hello"          # 渲染时使用的短文本快照

        # Mock _cursor_visual_pos_from_cache 返回 (0, 0)
        with patch.object(self.bb, '_cursor_visual_pos_from_cache', return_value=(0, 0)):
            r_cursor, _ = self.bb.compute_cursor_position(
                text_snapshot, cursor_pos=5, h=30, w=80,
            )

        # text_snapshot 为短文本，total_bottom = max(5, 5) = 5
        # r_cursor = max(1, 30 - 5 + 4 + 0 + 0 + 0) = 29
        self.assertEqual(r_cursor, 29,
                         "短文本快照下 r_cursor 应为 29（基于 text 参数）")

        # 验证：如果错误地使用 _last_text（长文本），r_cursor 会不同
        # total_bottom 基于 _last_text="A"*500 → 11 行
        # r_cursor_wrong = max(1, 30 - 11 + 4 + 0 + 0 + 0) = 23
        # 29 ≠ 23，证明修复有效
        self.assertNotEqual(r_cursor, 23,
                            "r_cursor 不应基于 _last_text 计算（29 ≠ 23）")

    def test_compute_cursor_position_text_equals_last_text(self):
        """text 参数与 _last_text 相同时 → 计算结果不变（向后兼容）。"""
        same_text = "hello world"
        self.bb._last_text = same_text

        with patch.object(self.bb, '_cursor_visual_pos_from_cache', return_value=(0, 5)):
            r_cursor, cursor_col = self.bb.compute_cursor_position(
                same_text, cursor_pos=5, h=30, w=80,
            )

        self.assertEqual(r_cursor, 29,
                         "text == _last_text 时应返回正确位置")
        self.assertEqual(cursor_col, 8,
                         "光标列 = 3 + vis_col(5) = 8")

    # ── 场景 5：补全弹窗可见时底部行数包含弹窗高度 ────────

    def test_compute_bottom_lines_for_with_completion_popup(self):
        """补全弹窗可见时 _compute_bottom_lines_for 应包含弹窗高度。"""
        # 模拟补全弹窗占 6 行
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 6

        result = self.bb._compute_bottom_lines_for("test", 80)
        # base = 1, return 4 + 0 + 1 + 6 = 11
        self.assertEqual(result, 11,
                         "补全弹窗 6 行时底部行数应为 11（_MIN_INPUT_ROWS=1）")

    def test_compute_cursor_position_with_completion_popup(self):
        """compute_cursor_position 在补全弹窗可见时应正确偏移光标行。"""
        self.bb._completion._visible = True
        self.bb._completion._popup_height = 4

        with patch.object(self.bb, '_cursor_visual_pos_from_cache', return_value=(0, 0)):
            r_cursor, _ = self.bb.compute_cursor_position(
                "test", cursor_pos=0, h=30, w=80,
            )

        # total_bottom = max(7, 4 + 0 + 1 + 4) = max(7, 9) = 9
        # r_cursor = max(1, 30 - 9 + 4 + 0 + 4 + 0) = 29
        self.assertEqual(r_cursor, 29,
                         "补全弹窗 4 行时 r_cursor 应正确偏移（_MIN_INPUT_ROWS=1）")

    # ── 场景 6：_compute_bottom_lines_for 不产生副作用 ──────

    def test_compute_bottom_lines_for_is_pure(self):
        """_compute_bottom_lines_for 不修改实例状态（幂等性）。"""
        # 记录调用前的关键状态
        last_text_before = self.bb._last_text
        subagent_before = list(self.bb._subagent_lines)
        completion_height_before = self.bb._completion.height

        # 多次调用
        result1 = self.bb._compute_bottom_lines_for("hello", 80)
        result2 = self.bb._compute_bottom_lines_for("world", 80)
        result3 = self.bb._compute_bottom_lines_for("hello", 80)

        # 验证返回值一致
        self.assertEqual(result1, result3,
                         "相同输入应返回相同结果（幂等）")
        self.assertEqual(result1, 5)
        self.assertEqual(result2, 5)

        # 验证实例状态未被修改
        self.assertEqual(self.bb._last_text, last_text_before,
                         "_last_text 不应被修改")
        self.assertEqual(list(self.bb._subagent_lines), subagent_before,
                         "_subagent_lines 不应被修改")
        self.assertEqual(self.bb._completion.height, completion_height_before,
                         "_completion.height 不应被修改")

    # ── 场景 7：subagent 面板行计入底部行数 ──────────────────

    def test_compute_bottom_lines_for_with_subagent_lines(self):
        """subagent 面板行应计入 _compute_bottom_lines_for 返回值。"""
        self.bb._subagent_lines = ["line1", "line2", "line3"]

        result = self.bb._compute_bottom_lines_for("test", 80)
        # 4 + 3 + 1 + 0 = 8
        self.assertEqual(result, 8,
                         "3 行 subagent 面板时底部行数应为 8（_MIN_INPUT_ROWS=1）")

    # ── 场景 8：极端终端宽度 ──────────────────────────────

    def test_compute_bottom_lines_for_extreme_width(self):
        """极端终端宽度（width=1）下 _compute_bottom_lines_for 应正常计算。"""
        # term_width=1 → max_input = max(1, 1-4) = 1
        # 每个字符一行
        result = self.bb._compute_bottom_lines_for("abc", 1)
        # expanded = "abc", wrapped in width=1 → ["a","b","c"], len=3
        # base = max(1, 3) = 3, return 4 + 0 + 3 + 0 = 7
        self.assertEqual(result, 7,
                         "width=1 时应正常计算（每个字符一行，base=3，+2 顶底分割线）")


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
        self.bb._tool_total = 5

        # mock _get_snapshot 返回模拟的统计数据
        mock_snap = {
            "total_tokens": 1500,
            "elapsed_seconds": 12.5,
            "per_second_speed": 25.3,
        }

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        # 应包含模型名 + 统计信息
        self.assertIn("test-model", result)
        self.assertIn("t", result)      # token 数
        self.assertIn("12.5s", result)  # 耗时
        self.assertIn("25.3t/s", result)  # 速率
        # 应包含运行中工具计数格式（⚙ <运行中>→<总数>）
        self.assertIn("\u2192", result,  "应包含运行中工具→总数分隔符")

    def test_streaming_inactive_hides_stats_even_with_tool_count(self):
        """非流式空闲 _status_active=False，即使 _tool_count>0 → 仅模型名。"""
        self.bb._status_active = False
        self.bb._tool_count = 3
        self.bb._tool_fail_count = 1

        # mock snapshot 有数据，但 _status_active=False 时应跳过
        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=None):
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

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=None):
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

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
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

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        self.assertIn("test-model", result)
        self.assertNotIn("t/s", result)

    def test_tool_count_running_shows_arrow_format(self):
        """_tool_count>0 且 _tool_fail_count=0 → ⚙ <运行中>→<总数> 格式（绿色总数）。"""
        self.bb._status_active = True
        self.bb._tool_count = 2
        self.bb._tool_fail_count = 0
        self.bb._tool_total = 5

        mock_snap = {
            "total_tokens": 500,
            "elapsed_seconds": 5.0,
            "per_second_speed": 10.0,
        }

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        # 应包含青色运行中计数 [38;5;45m2
        self.assertIn("[38;5;45m2", result,
                      "运行中计数 2 应使用 CYAN_256(45)")
        # 应包含 → 分隔符
        self.assertIn("\u2192", result,
                      "应包含运行中→总数分隔符")
        # 应包含绿色总数 [38;5;41m5（无失败时）
        self.assertIn("[38;5;41m5", result,
                      "总数 5 无失败时应使用 GREEN_256(41)")

    def test_tool_count_zero_keeps_original_format(self):
        """_tool_count=0 且 _tool_fail_count>0 → 保持原有 done/total 格式（无→）。"""
        self.bb._status_active = True
        self.bb._tool_count = 0
        self.bb._tool_fail_count = 2
        self.bb._tool_total = 5

        mock_snap = {
            "total_tokens": 500,
            "elapsed_seconds": 5.0,
            "per_second_speed": 10.0,
        }

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        # 应包含 done=3 绿色 + / 分隔 + total=5 红色（3/5 格式）
        self.assertIn("[38;5;41m3", result,
                      "done=3 应使用 GREEN_256(41)")
        self.assertIn("[38;5;196m5", result,
                      "total=5 有失败时应使用 TOOL_FAIL(196)")
        # 不应包含 → 分隔符
        self.assertNotIn("\u2192", result,
                         "_tool_count=0 时不应包含→分隔符")

    def test_tool_count_running_with_failures_shows_fail_total(self):
        """_tool_count>0 且 _tool_fail_count>0 → ⚙ <运行中>→<总数>（红色总数）。"""
        self.bb._status_active = True
        self.bb._tool_count = 2
        self.bb._tool_fail_count = 1
        self.bb._tool_total = 5

        mock_snap = {
            "total_tokens": 500,
            "elapsed_seconds": 5.0,
            "per_second_speed": 10.0,
        }

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
            result = self.bb._format_status()

        # 应包含青色运行中计数 [38;5;45m2
        self.assertIn("[38;5;45m2", result,
                      "运行中计数 2 应使用 CYAN_256(45)")
        # 应包含 → 分隔符
        self.assertIn("\u2192", result,
                      "应包含运行中→总数分隔符")
        # 应包含红色总数 [38;5;196m5（有失败时）
        self.assertIn("[38;5;196m5", result,
                      "总数 5 有失败时应使用 TOOL_FAIL(196)")


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
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
            self.bb.setup()

        expected = 30 - (2 + max(1, 0) + 2)  # height - (5) = 25
        self.assertEqual(self.bb._last_scroll_end, 25,
                         "setup() 后 _last_scroll_end 应为 25 (30-5)")

    def test_ensure_cursor_upper_uses_cached_value(self):
        """ensure_cursor_in_upper() 使用 _last_scroll_end 而非动态计算。"""
        self.bb._active = True
        self.bb._last_scroll_end = 25  # 模拟 setup 后的值
        self.bb._last_text = "x" * 300  # 长文本使 _bottom_lines 很大

        # Blessed 在非 TTY 环境下返回空字符串，需 patch get_terminal
        mock_term = _mock_terminal(width=80, height=30)
        with patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
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
        with patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
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
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
            self.bb.sync_bottom_lines()

        # _bottom_lines = 2 + 2 + max(1, 0) + 6 = 11（_MIN_INPUT_ROWS=1）
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
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
            self.bb.sync_bottom_lines()

        output = out.getvalue()
        self.assertEqual(output, "",
                         "_bottom_lines 未变时 sync_bottom_lines 不应输出 ANSI 序列")

    def test_sync_bottom_lines_shrink_clears_interval(self):
        """终端缩小后 sync_bottom_lines 应保护上屏内容不清除 + DECSTBM 正确更新。

        ★ Resize 保护：终端缩小后 sync_bottom_lines 跳过清除 scroll_end 行和
        上屏区域行（这些行由上屏内容保留），仅更新 DECSTBM 滚动区域。 
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
             patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
            self.bb.sync_bottom_lines()

        output = out.getvalue()
        # ★ Resize 保护：缩小场景不清除任何上屏区域行（行 21+）
        # scroll_end = 25 - 5 = 20
        for r in range(21, 26):
            self.assertNotIn(f"\033[{r};1H\033[K", output,
                             f"终端缩小后应保护上屏内容不清除行 {r}")
        # DECSTBM 应更新为 (1, 20)
        self.assertIn("\033[1;20r", output,
                      "终端缩小后 DECSTBM 应更新为 (1, 20)")


class TestDrainQueueSyncBottomLines(unittest.TestCase):
    """验证 _drain_queue() Stage 1 非 resize 时调用 sync_bottom_lines()。"""

    def setUp(self):
        from src.tui.engine.engine import RenderEngine
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
        from src.tui.engine.const import RenderCommand
        self.engine.push_cmd((RenderCommand.NOTIFICATION, "test"))

    def test_not_resized_calls_sync_bottom_lines(self):
        """resized=False 时 sync_bottom_lines 应在 ensure_cursor_upper 之前被调用。"""
        self._enqueue_cmd()
        self.mock_bb.check_resize.return_value = False

        with \
             patch("src.tui.widgets.lock._try_acquire_output_lock",
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
             patch("src.tui.widgets.lock._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=True),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(sys, '__stdout__', MagicMock()):
            self.engine._drain_queue()

        self.mock_bb.sync_bottom_lines.assert_not_called()
        self.mock_bb.ensure_cursor_in_upper.assert_not_called()


class TestApplyScrollDeltaOrdering(unittest.TestCase):
    """验证 2026-06-28 修复后的 force_redraw 行为。

    2026-06-28 修复：底部栏扩大时（delta > 0），在内容区 DECSTBM 内
    做 SU 上滚以腾出空间，内容整体上移而不被底部栏覆盖。
    底部栏缩小时直接清除释放区域行。
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
            with patch("src.tui.widgets.bottom_bar.bar.get_terminal", return_value=mock_term):
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

    def test_force_redraw_expand_does_su(self):
        """★ 2026-06-28 修复: force_redraw 在 delta > 0 时执行 SU 上滚序列。

        底部栏扩大时，在临时 DECSTBM [1, old_scroll_end] 内做 SU(delta)
        将内容区整体上移，底部留出空白行供底部栏使用，避免直接覆盖上屏内容。

        验证：① SU 序列存在；② \\033[r 存在（重置后）；③ 新 DECSTBM 正确设置。
        """
        self.bb._last_text = "A" * 500  # 长文本，_bottom_lines 会增大
        self.bb._last_bottom_lines = 3  # 旧底部行数较小
        self.bb._last_rendered_text = "old"

        output = self._capture_ansi_order(lambda: self.bb.force_redraw())

        import re
        # ★ 验证 SU 序列存在（delta > 0 时上滚内容）
        su_match = re.search(r'\x1b\[(\d+)S', output)
        self.assertIsNotNone(su_match, "delta > 0 时应输出 SU 上滚序列")
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
        # ★ 验证新 DECSTBM 已设置: scroll_end = 30-5 = 25
        self.assertIn("\033[1;25r", output, "应设置新 DECSTBM [1;25r")
        # ★ 验证清除操作发生在 DECSTBM 之后（回收区域 23-30，跳过 r1 分隔线行 26）
        decstbm_pos = output.index("\033[1;25r")
        for r in list(range(23, 26)) + list(range(27, 31)):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"应清除回收区域行 {r}")
        # 验证清除在 DECSTBM 之后
        seq_23 = f"\033[23;1H\033[K"
        if seq_23 in output:
            self.assertGreater(output.index(seq_23), decstbm_pos,
                               "回收行 23 的清除应在 DECSTBM 之后")

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
        # 定位到终端末行：\033[25;1H（新终端高度）
        self.assertIn("\033[25;1H", output,
                      "shrink 路径应使用新 height(25) 定位光标到终端末行")




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
        # delta = (2 + 2 + 1 + 0) - 9 = 5 - 9 = -4
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
        # ★ 验证新 DECSTBM 已设置: scroll_end = 30-5 = 25
        self.assertIn("\033[1;25r", output, "应设置新 DECSTBM [1;25r")
        # ★ 验证清除操作发生在 DECSTBM 之后
        # 检查回收区域行 23（delta<0 路径的行）确保在 DECSTBM 之后
        decstbm_pos = output.index("\033[1;25r")
        seq_23 = f"\033[23;1H\033[K"
        if seq_23 in output:
            self.assertGreater(output.index(seq_23), decstbm_pos,
                               "回收行 23 的清除应在 DECSTBM 之后")
        # 验证其他回收行（25-30）被清除（可能位于 DECSTBM 之前或之后）
        for r in range(25, 31):
            self.assertIn(f"\033[{r};1H\033[K", output,
                          f"应清除回收区域行 {r}")

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
        # show 设置 DECSTBM 并重绘底部栏，delta>0 时执行 SU 上滚内容
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
        # ★ 验证 delta<0 释放区域被清空（行 21-25，即 old_scroll_end+1 到 scroll_end）
        # old_scroll_end = 30 - 10 = 20, scroll_end = 30 - 5 = 25
        for r in range(21, 26):
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

class TestForceRedrawFullRepaintGhosting(unittest.TestCase):
    """★ P0-1: full_repaint 模式下清除整个底部栏区域，消除 resize 后鬼影。

    修复前：clear_start = max(old_scroll_end, scroll_end) + 1，
    当 old_scroll_end > scroll_end 时（底部栏扩大），clear_start 过大，
    导致底部栏区域未被显式清除，旧内容残留形成鬼影。
    修复后：full_repaint 模式下 clear_start = scroll_end + 1，
    清除整个新底部栏区域（scroll_end+1 ~ height），不触及上屏内容区。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = "A" * 500  # 长文本，total 增大
        self.bb._last_bottom_lines = 5  # 旧底部行数（较小）
        self.bb._last_rendered_text = "old"
        self.bb._last_height = 30
        self.bb._last_status = ""
        self.bb._subagent_lines = []
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_full_repaint_clears_entire_bottom_bar(self):
        """full_repaint 模式下清除 scroll_end+1 到 height 的所有行。

        场景：old_scroll_end=25 > scroll_end=19（底部栏扩大导致 scroll_end 减小）。
        修复前 clear_start=max(25,21)+1=26，仅清除 26-30。
        修复后 clear_start=19+1=20，清除 20-30（含 20-25 的旧内容区残留）。

        验证方式：行 20（scroll_end+1，也是 separator 所在行）在修复后
        被 clear block 显式清除一次，然后又被 separator draw 清除一次，
        共 2 次。修复前仅 1 次（仅 separator draw）。
        """
        self.bb._needs_full_repaint = True

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        output = buf.getvalue()
        # total = 2 + 2 + 7 + 0 = 11, scroll_end = 30 - 11 = 19
        # old_scroll_end = 30 - 5 = 25
        # r1 (separator) = 20
        # 行 20 应被 clear block + separator draw 共清除 2 次
        count_20 = output.count("\033[20;1H\033[K")
        self.assertGreaterEqual(count_20, 2,
            "full_repaint 模式下行 20 应被显式清除（clear block + separator draw ≥ 2次）")

    def test_full_repaint_does_not_clear_upper_content(self):
        """full_repaint 模式下不清除上屏内容区（1 ~ scroll_end）。"""
        self.bb._needs_full_repaint = True

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        output = buf.getvalue()
        # scroll_end = 19, 上屏内容区为 1-19
        for r in range(1, 20):
            self.assertNotIn(f"\033[{r};1H\033[K", output,
                f"full_repaint 模式下不应清除上屏内容区行 {r}")


class TestScrollEndEarlyReturnDecstbm(unittest.TestCase):
    """★ P1-3: scroll_end < 1 早退路径同步 _last_scroll_end 和 tracker。

    修复前：scroll_end < 1 早退路径不更新 _last_scroll_end，
    后续 ensure_cursor_in_upper() 使用过期的 _last_scroll_end 定位光标。
    修复后：早退路径设置 _last_scroll_end = height 并同步 tracker。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = "A" * 1000  # 长文本使 total >> height
        self.bb._last_bottom_lines = 5
        self.bb._last_rendered_text = "old"
        self.bb._last_height = 30
        self.bb._last_scroll_end = 25  # 旧值
        self.bb._last_status = ""
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_early_return_updates_last_scroll_end(self):
        """scroll_end < 1 早退时 _last_scroll_end 应更新为 height。"""
        # height=10, total=16 → scroll_end = -6 < 1
        mock_tracker = MagicMock()
        self.bb._tracker = mock_tracker

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=10), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        self.assertEqual(self.bb._last_scroll_end, 10,
            "scroll_end < 1 早退时 _last_scroll_end 应更新为 height(10)")

    def test_early_return_updates_tracker(self):
        """scroll_end < 1 早退时 tracker.set_scroll_end(height) 应被调用。"""
        mock_tracker = MagicMock()
        self.bb._tracker = mock_tracker

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=10), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        mock_tracker.set_scroll_end.assert_called_once_with(10)

    def test_early_return_without_tracker(self):
        """tracker 为 None 时早退路径不抛异常。"""
        self.bb._tracker = None

        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch.object(self.bb, '_term_height', return_value=10), \
             patch.object(self.bb, '_term_width', return_value=80), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()

        self.assertEqual(self.bb._last_scroll_end, 10,
            "tracker=None 时早退路径仍应更新 _last_scroll_end")


class TestEnsureCursorInLowerLocking(unittest.TestCase):
    """★ P1-5: ensure_cursor_in_lower 加锁 + 使用 _last_rendered_text。

    修复前：无锁直写 stdout，且使用 _last_text（可能与屏幕渲染不一致）。
    修复后：通过 _try_acquire_output_lock 加锁，使用 _last_rendered_text
    确保光标定位与屏幕显示的文本布局一致。
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = "A" * 500  # EscapeMonitor 最新值（长文本）
        self.bb._last_rendered_text = "hello"  # 屏幕实际渲染的文本（短文本）
        self.bb._input_cursor_pos = 3
        self.bb._last_bottom_lines = 5
        self.bb._subagent_lines = []
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_uses_last_rendered_text(self):
        """ensure_cursor_in_lower 应使用 _last_rendered_text 而非 _last_text。"""
        captured_text = []

        def capture_compute(text, cursor_pos, max_width):
            captured_text.append(text)
            return (0, 0)

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch("src.tui.widgets.bottom_bar.bar._compute_cursor_visual_pos",
                   side_effect=capture_compute), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80):
            self.bb.ensure_cursor_in_lower()

        self.assertEqual(len(captured_text), 1,
            "应调用一次 _compute_cursor_visual_pos")
        self.assertEqual(captured_text[0], "hello",
            "应使用 _last_rendered_text('hello') 而非 _last_text('A'*500)")

    def test_falls_back_to_last_text_when_rendered_empty(self):
        """_last_rendered_text 为空时降级使用 _last_text。"""
        self.bb._last_rendered_text = ""
        self.bb._last_text = "fallback"
        captured_text = []

        def capture_compute(text, cursor_pos, max_width):
            captured_text.append(text)
            return (0, 0)

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch("src.tui.widgets.bottom_bar.bar._compute_cursor_visual_pos",
                   side_effect=capture_compute), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80):
            self.bb.ensure_cursor_in_lower()

        self.assertEqual(captured_text[0], "fallback",
            "_last_rendered_text 为空时应降级使用 _last_text")

    def test_lock_timeout_skips_output(self):
        """锁超时时 ensure_cursor_in_lower 不应输出任何 ANSI 序列。"""
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("src.tui.widgets.bottom_bar.bar._try_acquire_output_lock",
                   return_value=MagicMock(__enter__=MagicMock(return_value=False),
                                         __exit__=MagicMock(return_value=False))), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80):
            self.bb.ensure_cursor_in_lower()

        self.assertEqual(buf.getvalue(), "",
            "锁超时时不应输出任何 ANSI 序列")

    def test_cursor_pos_clamped_to_text_length(self):
        """cursor_pos 应被 clamp 到文本长度，防止越界。"""
        self.bb._last_rendered_text = "hi"  # len=2
        self.bb._input_cursor_pos = 100  # 超出文本长度
        captured_pos = []

        def capture_compute(text, cursor_pos, max_width):
            captured_pos.append(cursor_pos)
            return (0, 0)

        with patch.object(sys, '__stdout__', io.StringIO()), \
             patch("src.tui.widgets.bottom_bar.bar._compute_cursor_visual_pos",
                   side_effect=capture_compute), \
             patch.object(self.bb, '_term_height', return_value=30), \
             patch.object(self.bb, '_term_width', return_value=80):
            self.bb.ensure_cursor_in_lower()

        self.assertEqual(captured_pos[0], 2,
            "cursor_pos 应被 clamp 到 len('hi')=2")

    def test_inactive_skips(self):
        """_active=False 时直接返回，不获取锁。"""
        self.bb._active = False
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf):
            self.bb.ensure_cursor_in_lower()
        self.assertEqual(buf.getvalue(), "",
            "_active=False 时不应有输出")


class TestBottomBarColorAlignment(unittest.TestCase):
    """验证底部栏 _COLOR_* 常量与统一 256 色体系对齐。

    步骤 8 颜色对齐要点：
      1. _COLOR_ACCENT → 45 (CYAN_256)
      2. _COLOR_DIM → 242 (GRAY_256)
      3. _COLOR_TOOL_OK → 41 (GREEN_256)
      4. _COLOR_TOOL_FAIL → 196 (RED_256)
      5. _COLOR_COMPLETE_MATCH → 221 (YELLOW_256)
      6. _COLOR_SELECT_BG → 236
      7. _format_status 输出含 256 色码
    """

    def test_color_accent_is_45(self):
        """_COLOR_ACCENT 应为 CYAN_256(45)。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_ACCENT
        self.assertIn("38;5;45", _COLOR_ACCENT,
                      "_COLOR_ACCENT 应对齐 CYAN_256(45)")

    def test_color_dim_is_242(self):
        """_COLOR_DIM 应为 GRAY_256(242)。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_DIM
        self.assertIn("38;5;242", _COLOR_DIM,
                      "_COLOR_DIM 应对齐 GRAY_256(242)")

    def test_color_tool_ok_is_41(self):
        """_COLOR_TOOL_OK 应为 GREEN_256(41)。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_TOOL_OK
        self.assertIn("38;5;41", _COLOR_TOOL_OK,
                      "_COLOR_TOOL_OK 应对齐 GREEN_256(41)")

    def test_color_tool_fail_is_196(self):
        """_COLOR_TOOL_FAIL 应为 RED_256(196)。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_TOOL_FAIL
        self.assertIn("38;5;196", _COLOR_TOOL_FAIL,
                      "_COLOR_TOOL_FAIL 应对齐 RED_256(196)")

    def test_color_complete_match_is_221(self):
        """_COLOR_COMPLETE_MATCH 应为 YELLOW_256(221)。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_COMPLETE_MATCH
        self.assertIn("38;5;221", _COLOR_COMPLETE_MATCH,
                      "_COLOR_COMPLETE_MATCH 应对齐 YELLOW_256(221)")

    def test_color_select_bg_is_236(self):
        """_COLOR_SELECT_BG 应为 236。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_SELECT_BG
        self.assertIn("48;5;236", _COLOR_SELECT_BG,
                      "_COLOR_SELECT_BG 应为 236")

    def test_color_deep_cyan_is_32(self):
        """_COLOR_DEEP_CYAN 应为 32。"""
        from src.tui.widgets.bottom_bar.theme import _COLOR_DEEP_CYAN
        self.assertIn("38;5;32", _COLOR_DEEP_CYAN,
                      "_COLOR_DEEP_CYAN 应为 32")

    def test_format_status_contains_256_color(self):
        """_format_status 输出应含 256 色码。"""
        bb = _BottomBar()
        bb._active = True
        bb._status_active = True
        bb._model_name = "test-model"
        bb._tool_count = 2
        bb._tool_fail_count = 0
        bb._tool_total = 2

        mock_snap = {
            "total_tokens": 500,
            "elapsed_seconds": 5.0,
            "per_second_speed": 10.0,
        }

        with patch("src.tui.widgets.bottom_bar.status._get_snapshot", return_value=lambda: mock_snap):
            result = bb._format_status()

        # 应包含 256 色 ANSI 码（38;5;）
        self.assertIn("38;5;", result,
                      "_format_status 应含 256 色序列")
        # 应包含 CYAN_256(45) 色用于模型名
        self.assertIn("[38;5;45m", result,
                      "_format_status 应含 CYAN_256(45) 色")
        # 应包含 CYAN_256(45) 色用于运行中工具计数
        self.assertIn("[38;5;45m2", result,
                      "_format_status 应含 CYAN_256(45) 运行中计数 2")
        # 应包含运行中→总数分隔符
        self.assertIn("\u2192", result,
                      "_format_status 应含运行中→总数分隔符")
        # 应包含 GREEN_256(41) 色用于工具总数（无失败时）
        self.assertIn("[38;5;41m", result,
                      "_format_status 应含 GREEN_256(41) 色")


class TestPlaceholderGlow(unittest.TestCase):
    """验证底部栏占位符呼吸效果（步骤 5：主题色联动 glow 呼吸）。

    核心场景：
      1. 宽屏：占位符使用 build_glow_ansi 产生呼吸 ANSI（38;5;xxm）
      2. 窄屏：占位符使用静态 _COLOR_DIM（\033[38;5;242m）
    """

    def setUp(self):
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._last_text = ""           # 空文本 → 占位符路径
        self.bb._input_cursor_pos = 0
        self.bb._last_cursor_pos = 0
        self.bb._last_rendered_text = ""
        self.bb._status_active = False
        self.bb._model_name = "test-model"
        self.bb._subagent_lines = []
        self._stdout = sys.__stdout__

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_placeholder_glow_wide(self):
        """宽屏时占位符应包含呼吸 ANSI 256 色序列（38;5;）。"""
        self.bb._last_text = ""
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("src.tui.terminal.terminal.is_narrow", return_value=False), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()
        output = buf.getvalue()
        # 宽屏占位符路径应产生 256 色 ANSI 序列（build_glow_ansi）
        self.assertIn("38;5;", output,
                      "宽屏占位符应含 256 色 ANSI 呼吸序列")

    def test_placeholder_static_narrow(self):
        """窄屏时占位符使用静态 _COLOR_DIM（\033[38;5;242m）。"""
        self.bb._last_text = ""
        buf = io.StringIO()
        with patch.object(sys, '__stdout__', buf), \
             patch("src.tui.terminal.terminal.is_narrow", return_value=True), \
             patch.object(self.bb, '_format_status', return_value=""):
            self.bb.force_redraw()
        output = buf.getvalue()
        # 窄屏占位符应使用 _COLOR_DIM = \033[38;5;242m
        self.assertIn("\033[38;5;242m", output,
                      "窄屏占位符应使用 _COLOR_DIM(242)")


class TestGetSnapshot(unittest.TestCase):
    """验证 _get_snapshot() 真实导入路径正确性。

    核心场景：
      1. 直接调用 _get_snapshot()（不 mock），验证返回值是 callable 而非 None
      2. 验证修复后 ....api.stats 路径能正确加载 get_token_speed_snapshot
    """

    def setUp(self):
        # 强制重置模块级缓存变量，确保 _get_snapshot() 走真实导入路径
        import src.tui.widgets.bottom_bar.status as status_mod
        status_mod._TOKEN_SPEED_SNAPSHOT = None

    def test_get_snapshot_imports_correctly(self):
        """_get_snapshot() 应返回 callable 而非 None（验证真实导入路径正确）。"""
        from src.tui.widgets.bottom_bar import status

        # 强制重置缓存，使 _get_snapshot 走 try 导入路径
        status._TOKEN_SPEED_SNAPSHOT = None

        result = status._get_snapshot()

        self.assertTrue(callable(result),
                        "_get_snapshot() 应返回 callable 函数引用，而非 None；"
                        "若返回 None 说明导入路径仍不正确或 api.stats 模块缺失")


if __name__ == "__main__":
    unittest.main()
