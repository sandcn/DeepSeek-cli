"""底部栏状态分隔线测试 — 验证 _build_status_text() 和状态分隔线构建。

测试策略：
  1. _build_status_text() 四种状态文本正确定映射
  2. 工具调用中优先级高于主Agent阶段
  3. 空状态/零 start_time 返回空字符串
  4. _build_sep_with_system_stats() 状态分隔线 vs 纯渐变策略
  5. 窄屏模式回退纯渐变
  6. 中文 _visible_width() 正确计算
"""

from __future__ import annotations

import io
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from src.tui.widgets.bottom_bar.draw import (
    _build_sep_with_system_stats,
    _build_status_text,
    _visible_width,
)
from src.tui.widgets.bottom_bar.theme import _COLOR_ACCENT, _COLOR_RESET


class TestBuildStatusText(unittest.TestCase):
    """验证 _build_status_text() 的状态文本生成逻辑。"""

    def _make_bar(
        self,
        tool_count: int = 0,
        tool_phase_start: float = 0.0,
        main_phase: str = "",
        main_phase_start: float = 0.0,
    ) -> MagicMock:
        """构造 mock bar 对象。"""
        bar = MagicMock()
        bar._tool_count = tool_count
        bar._tool_phase_start = tool_phase_start
        bar._main_phase = main_phase
        bar._main_phase_start = main_phase_start
        return bar

    def test_build_status_text_thinking(self):
        """_main_phase="thinking" + 有效 start_time → 返回含"思考"的文本。"""
        bar = self._make_bar(
            main_phase="thinking",
            main_phase_start=time.monotonic() - 0.5,
        )
        result = _build_status_text(bar)
        self.assertIn("思考", result)
        self.assertIn("·", result)
        self.assertRegex(result, r"· 思考 \d+\.\d+s")

    def test_build_status_text_answering(self):
        """_main_phase="answering" → 返回含"回答"的文本。"""
        bar = self._make_bar(
            main_phase="answering",
            main_phase_start=time.monotonic() - 1.2,
        )
        result = _build_status_text(bar)
        self.assertIn("回答", result)
        self.assertRegex(result, r"· 回答 \d+\.\d+s")

    def test_build_status_text_parsing(self):
        """_main_phase="parsing" → 返回含"接收工具参数"的文本。"""
        bar = self._make_bar(
            main_phase="parsing",
            main_phase_start=time.monotonic() - 0.1,
        )
        result = _build_status_text(bar)
        self.assertIn("接收工具参数", result)
        self.assertRegex(result, r"· 接收工具参数 \d+\.\d+s")

    def test_build_status_text_tool_running(self):
        """_tool_count > 0 → 返回"工具调用中"（优先级高于 phase）。"""
        bar = self._make_bar(
            tool_count=3,
            tool_phase_start=time.monotonic() - 2.0,
            # 即使同时设置了 thinking phase，工具调用中优先级更高
            main_phase="thinking",
            main_phase_start=time.monotonic() - 5.0,
        )
        result = _build_status_text(bar)
        self.assertIn("工具调用中", result)
        self.assertNotIn("思考", result, "工具调用中应覆盖思考阶段")
        self.assertRegex(result, r"· 工具调用中 \d+\.\d+s")

    def test_build_status_text_tool_running_priority(self):
        """工具调用中覆盖 thinking 阶段（显式验证优先级）。"""
        bar = self._make_bar(
            tool_count=1,
            tool_phase_start=time.monotonic() - 0.8,
            main_phase="thinking",
            main_phase_start=time.monotonic() - 3.0,
        )
        result = _build_status_text(bar)
        self.assertIn("工具调用中", result)

    def test_build_status_text_empty(self):
        """_tool_count=0 且 _main_phase="" → 返回空字符串。"""
        bar = self._make_bar(
            tool_count=0,
            main_phase="",
        )
        result = _build_status_text(bar)
        self.assertEqual(result, "")

    def test_build_status_text_unknown_phase(self):
        """_main_phase 不在 _PHASE_DISPLAY 中且 tool_count=0 → 返回空字符串。"""
        bar = self._make_bar(
            tool_count=0,
            main_phase="unknown_phase",
            main_phase_start=time.monotonic() - 0.5,
        )
        result = _build_status_text(bar)
        self.assertEqual(result, "")

    def test_build_status_text_zero_start_time(self):
        """_tool_phase_start=0.0 → 返回空字符串（未初始化保护）。"""
        bar = self._make_bar(
            tool_count=5,
            tool_phase_start=0.0,
        )
        result = _build_status_text(bar)
        self.assertEqual(result, "")

    def test_build_status_text_negative_start_time(self):
        """start_time 为负值 → 返回空字符串（异常值保护）。"""
        bar = self._make_bar(
            tool_count=1,
            tool_phase_start=-1.0,
        )
        result = _build_status_text(bar)
        self.assertEqual(result, "")

    def test_build_status_text_main_phase_zero_start(self):
        """_main_phase_start=0.0 且 _tool_count=0 → 返回空字符串。"""
        bar = self._make_bar(
            tool_count=0,
            main_phase="thinking",
            main_phase_start=0.0,
        )
        result = _build_status_text(bar)
        self.assertEqual(result, "")

    def test_build_status_text_no_ansi(self):
        """_build_status_text 返回纯文本，不含 ANSI 颜色序列。"""
        bar = self._make_bar(
            main_phase="thinking",
            main_phase_start=time.monotonic() - 0.3,
        )
        result = _build_status_text(bar)
        self.assertNotIn("\033[", result, "状态文本应为纯文本，不含 ANSI 序列")
        self.assertNotIn("38;5;", result)


class TestBuildSepWithStatus(unittest.TestCase):
    """验证 _build_sep_with_system_stats() 的状态分隔线构建。"""

    def test_sep_with_status_active(self):
        """status_active=True + status_text 非空 → 返回含状态文本的分隔线。"""
        result = _build_sep_with_system_stats(
            tw=80,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
            status_text="· 思考 0.32s",
            status_active=True,
        )
        # 应包含状态文本（被 _COLOR_ACCENT 包裹）
        self.assertIn("思考", result)
        self.assertIn(_COLOR_ACCENT, result)
        self.assertIn(_COLOR_RESET, result)
        # 应以 2 空格开头
        self.assertTrue(result.startswith("  "))

    def test_sep_with_status_inactive(self):
        """status_active=False → 返回纯渐变（不含状态文本）。"""
        result = _build_sep_with_system_stats(
            tw=80,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
            status_text="· 思考 0.32s",
            status_active=False,
        )
        self.assertNotIn("思考", result)
        # 应包含渐变分隔线字符
        self.assertIn("\u2501", result)

    def test_sep_with_status_narrow(self):
        """窄屏模式 → 即使 status_active=True 也回退纯渐变。"""
        result = _build_sep_with_system_stats(
            tw=40,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
            status_text="· 思考 0.32s",
            status_active=True,
            narrow=True,
        )
        self.assertNotIn("思考", result)

    def test_sep_with_status_empty_text(self):
        """status_text 为空 → 即使 status_active=True 也回退纯渐变。"""
        result = _build_sep_with_system_stats(
            tw=80,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
            status_text="",
            status_active=True,
        )
        # 渐变本身起始色即为 45（_COLOR_ACCENT），所以检查不含状态文本即可
        self.assertNotIn("思考", result, "status_text 为空时不应含状态文本")

    def test_sep_with_status_default_params(self):
        """不传 status_text/status_active → 保持原有行为（纯渐变）。"""
        result = _build_sep_with_system_stats(
            tw=80,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
        )
        # 默认参数：status_text="", status_active=False → 纯渐变
        # 渐变本身起始色为 45，与 _COLOR_ACCENT 相同，所以检查不含状态文本
        self.assertNotIn("思考", result, "默认参数不应含状态文本")
        self.assertIn("\u2501", result)

    def test_sep_status_contains_gradient_after_status(self):
        """状态分隔线：状态文本后有渐变分隔线填充剩余宽度。"""
        result = _build_sep_with_system_stats(
            tw=80,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
            status_text="· 思考 0.32s",
            status_active=True,
        )
        # 去除 ANSI 后，应包含 ━ 字符
        clean = re.sub(r'\033\[[0-9;]*m', '', result)
        self.assertIn("\u2501", clean)

    def test_sep_status_chinese_width_visible(self):
        """中文状态文本的视觉宽度通过 _visible_width 正确计算。

        验证：分隔线总视觉宽度 = tw（80）。
        组成：2（前导空格）+ status_visual_width(12) + 1（分隔空格）+ gradient(65) = 80
        """
        result = _build_sep_with_system_stats(
            tw=80,
            sep_start=45,
            cpu_percent=10.0,
            mem_percent=20.0,
            status_text="· 思考 0.32s",
            status_active=True,
        )
        clean = re.sub(r'\033\[[0-9;]*m', '', result)
        # 去除 ANSI 后，字符长度：2 + 10(status_text chars) + 1 + 65(gradient) = 78
        # 视觉宽度：2 + 12 + 1 + 65 = 80
        self.assertEqual(len(clean), 78,
                         f"分隔线字符长度应为 78, 实际 {len(clean)}")


class TestVisibleWidthChinese(unittest.TestCase):
    """验证 _visible_width() 对中文的宽度计算。"""

    def test_visible_width_chinese_two_chars(self):
        """每个中文字符视觉宽度为 2。"""
        w = _visible_width("思考")
        self.assertEqual(w, 4, f'"思考" 宽度应为 4, 实际 {w}')

    def test_visible_width_status_text(self):
        """完整状态文本 "· 思考 0.32s" 视觉宽度正确。"""
        w = _visible_width("· 思考 0.32s")
        # · = 1, 空格 = 1, 思 = 2, 考 = 2, 空格 = 1, 0.32s = 5 → 12
        self.assertEqual(w, 12, f'"· 思考 0.32s" 宽度应为 12, 实际 {w}')

    def test_visible_width_ascii(self):
        """纯 ASCII 文本宽度等于长度。"""
        w = _visible_width("hello")
        self.assertEqual(w, 5)

    def test_visible_width_mixed(self):
        """中英混合文本宽度正确。"""
        w = _visible_width("工具调用中 1.23s")
        # 工=2, 具=2, 调=2, 用=2, 中=2, 空格=1, 1.23s=5 → 16
        self.assertEqual(w, 16, f'"工具调用中 1.23s" 宽度应为 16, 实际 {w}')

    def test_visible_width_empty(self):
        """空字符串宽度为 0。"""
        w = _visible_width("")
        self.assertEqual(w, 0)

    def test_visible_width_with_ansi(self):
        """含 ANSI 转义序列时正确去除后计算宽度。"""
        colored = f"{_COLOR_ACCENT}思考{_COLOR_RESET}"
        w = _visible_width(colored)
        self.assertEqual(w, 4, f'含 ANSI 的"思考"宽度应为 4, 实际 {w}')


class TestBuildSepBackwardCompat(unittest.TestCase):
    """验证 _build_sep_with_system_stats() 向后兼容性。"""

    def test_existing_positional_call_still_works(self):
        """现有位置参数调用仍正常工作（新增参数均为关键字且有默认值）。"""
        result = _build_sep_with_system_stats(80, 45, 10.0, 20.0)
        # 应有有效输出
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)
        self.assertIn("\u2501", result)

    def test_existing_keyword_call_still_works(self):
        """现有关键字参数调用仍正常工作。"""
        result = _build_sep_with_system_stats(
            tw=80, sep_start=45, cpu_percent=10.0, mem_percent=20.0,
            narrow=False, breath_frame=5,
        )
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


class TestDrawSeparatorAndStatus(unittest.TestCase):
    """验证 _draw_separator_and_status() 的状态分隔线行为。

    核心场景：
      1. 宽屏 + _status_active=True → 分隔线输出含状态文本
      2. 宽屏 + _status_active=False → 分隔线输出不含状态文本（纯渐变）
      3. 窄屏 → 始终纯渐变分隔线（状态文本不传递）
    """

    def setUp(self):
        from src.tui.widgets.bottom_bar import _BottomBar
        self.bb = _BottomBar()
        self.bb._active = True
        self.bb._cached_cpu_percent = 10.0
        self.bb._cached_mem_percent = 20.0
        self.bb._subagent_lines = []
        self.bb._last_status = "test-model ·"
        self.bb._tool_count = 0
        self.bb._tool_phase_start = 0.0
        self.bb._main_phase = ""
        self.bb._main_phase_start = 0.0
        self.bb._status_active = False
        # Mock _cursor_tracker
        self.bb._cursor_tracker = MagicMock()
        # Mock _animator
        self.bb._animator = MagicMock()
        self.bb._animator.breath_frame = 0
        self._stdout = sys.__stdout__

        # Mock blessed Terminal for _blessed_move_clear
        self._mock_term = MagicMock()
        self._mock_term.move_xy = lambda x, y: f"\033[{y + 1};{x + 1}H"
        self._mock_term.clear_eol = "\033[K"

    def tearDown(self):
        sys.__stdout__ = self._stdout

    def test_draw_separator_status_active(self):
        """宽屏 + _status_active=True + thinking phase → 分隔线含状态文本。"""
        now = time.monotonic()
        self.bb._status_active = True
        self.bb._main_phase = "thinking"
        self.bb._main_phase_start = now - 0.5

        out = io.StringIO()
        _buf: list[str] = []
        with patch("src.tui.terminal.terminal.is_narrow", return_value=False), \
             patch("src.tui.widgets.bottom_bar.blessed.get_terminal", return_value=self._mock_term):
            self.bb._draw_separator_and_status(out, _buf, r1=20, subagent_start=21, r2=21, tw=80)

        # _buf 第一个元素应为分隔线行（含状态文本）
        sep_line = _buf[0]
        # 去除 ANSI 后检查内容
        clean = re.sub(r'\033\[[0-9;]*m', '', sep_line)
        self.assertIn("思考", clean,
                      'status_active=True 时分隔线应含状态文本「思考」')
        # 状态文本应由 _COLOR_ACCENT 包裹
        self.assertIn(_COLOR_ACCENT, sep_line,
                      "状态文本应由 _COLOR_ACCENT 着色")

    def test_draw_separator_status_inactive(self):
        """宽屏 + _status_active=False → 分隔线不含状态文本（纯渐变）。"""
        self.bb._status_active = False

        out = io.StringIO()
        _buf: list[str] = []
        with patch("src.tui.terminal.terminal.is_narrow", return_value=False), \
             patch("src.tui.widgets.bottom_bar.blessed.get_terminal", return_value=self._mock_term):
            self.bb._draw_separator_and_status(out, _buf, r1=20, subagent_start=21, r2=21, tw=80)

        sep_line = _buf[0]
        clean = re.sub(r'\033\[[0-9;]*m', '', sep_line)
        self.assertNotIn("思考", clean,
                         "status_active=False 时分隔线不应含状态文本")
        # 应包含纯渐变分隔线字符
        self.assertIn("\u2501", clean,
                      "分隔线应含 ━ 字符")

    def test_draw_separator_status_active_tool_running(self):
        """宽屏 + _status_active=True + tool_count>0 → 分隔线含"工具调用中"。"""
        now = time.monotonic()
        self.bb._status_active = True
        self.bb._tool_count = 3
        self.bb._tool_phase_start = now - 1.0

        out = io.StringIO()
        _buf: list[str] = []
        with patch("src.tui.terminal.terminal.is_narrow", return_value=False), \
             patch("src.tui.widgets.bottom_bar.blessed.get_terminal", return_value=self._mock_term):
            self.bb._draw_separator_and_status(out, _buf, r1=20, subagent_start=21, r2=21, tw=80)

        sep_line = _buf[0]
        clean = re.sub(r'\033\[[0-9;]*m', '', sep_line)
        self.assertIn("工具调用中", clean,
                      'tool_count>0 时分隔线应含「工具调用中」')

    def test_draw_separator_narrow_always_pure_gradient(self):
        """窄屏 → 即使 _status_active=True 也回退纯渐变。"""
        now = time.monotonic()
        self.bb._status_active = True
        self.bb._main_phase = "thinking"
        self.bb._main_phase_start = now - 0.5

        out = io.StringIO()
        _buf: list[str] = []
        with patch("src.tui.terminal.terminal.is_narrow", return_value=True), \
             patch("src.tui.widgets.bottom_bar.blessed.get_terminal", return_value=self._mock_term):
            self.bb._draw_separator_and_status(out, _buf, r1=20, subagent_start=21, r2=21, tw=40)

        sep_line = _buf[0]
        clean = re.sub(r'\033\[[0-9;]*m', '', sep_line)
        self.assertNotIn("思考", clean,
                         "窄屏模式下分隔线不应含状态文本")
        self.assertIn("\u2501", clean,
                      "窄屏模式下分隔线应含 ━ 字符")

    def test_draw_separator_status_writes_to_out(self):
        """验证 _draw_separator_and_status 将 _buf 内容写入 out。"""
        now = time.monotonic()
        self.bb._status_active = True
        self.bb._main_phase = "answering"
        self.bb._main_phase_start = now - 0.3

        out = io.StringIO()
        _buf: list[str] = []
        with patch("src.tui.terminal.terminal.is_narrow", return_value=False), \
             patch("src.tui.widgets.bottom_bar.blessed.get_terminal", return_value=self._mock_term):
            self.bb._draw_separator_and_status(out, _buf, r1=20, subagent_start=21, r2=21, tw=80)

        output = out.getvalue()
        self.assertNotEqual(output, "", "应写入内容到 out")
        # 输出应包含 _buf 全部内容（分隔线 + 状态行）
        # 注意：状态文本中有"回答"中文
        clean = re.sub(r'\033\[[0-9;]*m', '', output)
        self.assertIn("回答", clean,
                      '输出应含状态文本「回答」')

    def test_draw_separator_status_text_no_start_time_returns_empty(self):
        """_status_active=True 但 start_time <= 0 → _build_status_text 返回空 → 纯渐变。"""
        self.bb._status_active = True
        self.bb._main_phase = "thinking"
        self.bb._main_phase_start = 0.0  # 未初始化

        out = io.StringIO()
        _buf: list[str] = []
        with patch("src.tui.terminal.terminal.is_narrow", return_value=False), \
             patch("src.tui.widgets.bottom_bar.blessed.get_terminal", return_value=self._mock_term):
            self.bb._draw_separator_and_status(out, _buf, r1=20, subagent_start=21, r2=21, tw=80)

        sep_line = _buf[0]
        clean = re.sub(r'\033\[[0-9;]*m', '', sep_line)
        self.assertNotIn("思考", clean,
                         "start_time=0 时不应显示状态文本")
        self.assertIn("\u2501", clean,
                      "start_time=0 时应回退纯渐变")


if __name__ == "__main__":
    unittest.main()
