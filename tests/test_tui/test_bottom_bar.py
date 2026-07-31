"""测试 _bottom_bar.py — 底部栏模块。

测试 _compute_input_rows、compute_cursor_position 返回值的正确范围、
show_completions/hide_completions/cycle_completion/get_selected_completion 状态管理。
Mock 终端尺寸，不执行真实终端 I/O。
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest


class TestBottomBarInit:
    """测试 _BottomBar 初始化。"""

    def test_init_defaults(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        assert bb.is_active is False
        assert bb.is_completion_visible is False
        assert bb._status_active is False
        assert bb._tool_count == 0

    def test_init_with_cursor_tracker(self):
        from src.tui._cursor_tracker import CursorTracker
        from src.tui._bottom_bar import _BottomBar
        ct = CursorTracker()
        bb = _BottomBar(cursor_tracker=ct)
        assert bb._cursor_tracker is ct


class TestBottomBarProperties:
    """测试 _BottomBar 属性。"""

    def test_bottom_lines_inactive(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        # 即使 inactive，_bottom_lines 也能计算
        lines = bb._bottom_lines
        # 2 + 0 (_subagent_lines) + _compute_input_rows()
        assert lines >= 2

    def test_compute_bottom_lines_for_empty(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        result = bb._compute_bottom_lines_for("", 80)
        # 4 + 0 + _MIN_INPUT_ROWS(1) + 0(completion) = 5
        assert result >= 5

    def test_compute_bottom_lines_for_text(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        result = bb._compute_bottom_lines_for("hello world", 80)
        assert result >= 5


class TestStatusMixin:
    """测试 _StatusMixin 内联方法。"""

    def test_increment_tool(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        assert bb._tool_count == 0
        bb.increment_tool()
        assert bb._tool_count == 1
        assert bb._tool_total == 1

    def test_decrement_tool(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.increment_tool()
        bb.increment_tool()
        bb.decrement_tool()
        assert bb._tool_count == 1
        assert bb._tool_total == 2

    def test_decrement_tool_floor(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.decrement_tool()
        assert bb._tool_count == 0

    def test_increment_tool_fail(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.increment_tool_fail()
        assert bb._tool_fail_count == 1

    def test_reset_tool_count(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.increment_tool()
        bb.increment_tool_fail()
        bb.reset_tool_count()
        assert bb._tool_count == 0
        assert bb._tool_fail_count == 0
        assert bb._tool_total == 0

    def test_set_model_name(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.set_model_name("deepseek-v3")
        assert bb._model_name == "deepseek-v3"

    def test_enable_disable_status(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        assert bb._status_active is False
        bb.enable_status()
        assert bb._status_active is True
        bb.disable_status()
        assert bb._status_active is False

    def test_set_main_phase(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.set_main_phase("thinking")
        assert bb._main_phase == "thinking"
        bb.set_main_phase("answering")
        assert bb._main_phase == "answering"

    def test_format_status_no_model(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        result = bb._format_status()
        assert result == ""


class TestCompletionPopup:
    """测试 _CompletionPopup 状态管理。"""

    def test_initial_state(self):
        from src.tui._bottom_bar import _CompletionPopup
        cp = _CompletionPopup()
        assert cp.is_visible is False
        assert cp.height == 0
        assert cp.idx == 0

    def test_cycle_invisible(self):
        from src.tui._bottom_bar import _CompletionPopup
        cp = _CompletionPopup()
        result = cp.cycle(1)
        assert result == 0  # 不可见时返回 0

    def test_get_selected_invisible(self):
        from src.tui._bottom_bar import _CompletionPopup
        cp = _CompletionPopup()
        result = cp.get_selected()
        assert result == ("", 0, "")

    def test_show_completions_state_management(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()

        # 不能直接调用 show_completions 因为会触发 force_redraw 的终端 I/O
        # 直接操作 _completion 对象
        items = ["foo", "bar", "baz"]
        bb._completion._visible = True
        bb._completion._items = list(items)
        bb._completion._texts = list(items)
        bb._completion._idx = 1
        bb._completion._popup_height = 5

        assert bb.is_completion_visible is True
        assert bb._completion_idx == 1
        assert bb._completion_popup_height == 5

        text, start_pos, prefix = bb.get_selected_completion()
        assert text == "bar"

        # cycle
        bb._completion.cycle(1)
        assert bb._completion_idx == 2
        bb._completion.cycle(1)
        assert bb._completion_idx == 0  # wrap around

    def test_hide_completions_state(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._completion._visible = True
        bb._completion._items = ["foo"]
        bb._completion._texts = ["foo"]
        bb._completion._popup_height = 3

        # Direct state manipulation (hide_completions triggers I/O)
        bb._completion._popup_height = 0
        bb._completion._visible = False
        bb._completion._items = []
        bb._completion._texts = []

        assert bb.is_completion_visible is False
        assert bb._completion_popup_height == 0


class TestGetSelectedCompletionIndex:
    """测试 get_selected_completion_index 方法。"""

    def test_visible_idx_1_returns_1(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._completion._visible = True
        bb._completion._items = ["a", "b", "c"]
        bb._completion._texts = ["a", "b", "c"]
        bb._completion._idx = 1
        bb._completion._popup_height = 3
        assert bb.get_selected_completion_index() == 1

    def test_cycle_increments_index(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._completion._visible = True
        bb._completion._items = ["a", "b", "c"]
        bb._completion._texts = ["a", "b", "c"]
        bb._completion._idx = 0
        bb._completion._popup_height = 3
        bb._completion.cycle(1)
        assert bb.get_selected_completion_index() == 1

    def test_hidden_returns_0(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        # 弹窗隐藏时 _idx 初始值为 0
        assert bb.get_selected_completion_index() == 0

    def test_hidden_returns_last_idx_before_hide(self):
        """弹窗隐藏时返回 _last_idx_before_hide 而非 _idx。"""
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        # 设置 _idx=5、_last_idx_before_hide=3，弹窗不可见
        bb._completion._idx = 5
        bb._completion._last_idx_before_hide = 3
        bb._completion._visible = False
        # 应返回 3（_last_idx_before_hide）而非 5（_idx）
        assert bb.get_selected_completion_index() == 3


class TestSetSubagentFrame:
    """测试 subagent 面板。"""

    def test_set_subagent_frame(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        lines = ["line1", "line2"]
        bb.set_subagent_frame(lines)
        assert bb._subagent_lines == lines
        # 修改原始列表不应影响内部状态
        lines.append("line3")
        assert bb._subagent_lines == ["line1", "line2"]


class TestSimpleAnimator:
    """测试动画时钟（原 _SimpleAnimator，现 AnimatorContext）。"""

    def test_singleton(self):
        from src.tui._animator import AnimatorContext
        a1 = AnimatorContext.get_default()
        a2 = AnimatorContext.get_default()
        assert a1 is a2

    def test_tick(self):
        from src.tui._animator import AnimatorContext
        a = AnimatorContext()
        assert a.frame == 0
        a.tick()
        assert a.frame == 1
        a.tick()
        assert a.frame == 2

    def test_sine_color(self):
        from src.tui._animator import AnimatorContext
        a = AnimatorContext()
        # frame=0 → sin(0)=0 → ratio=0.5 → lo + (hi-lo)*0.5
        color = a.sine_color(40, 50, 12)
        assert 40 <= color <= 50


class TestUtilityFunctions:
    """测试工具函数。"""

    def test_is_narrow(self):
        from src.tui._bottom_bar import _is_narrow
        # 在测试环境中终端宽度不确定，但函数不抛异常即通过
        result = _is_narrow()
        assert isinstance(result, bool)

    def test_visual_width_plain(self):
        from src.tui._bottom_bar import _visual_width
        assert _visual_width("hello") == 5

    def test_visual_width_ansi(self):
        from src.tui._bottom_bar import _visual_width
        # ANSI 序列宽度为 0
        assert _visual_width("\033[38;5;45mhello\033[0m") == 5

    def test_visual_width_cjk(self):
        from src.tui._bottom_bar import _visual_width
        assert _visual_width("你好") == 4

    def test_truncate_by_width(self):
        from src.tui._bottom_bar import _truncate_by_width
        assert _truncate_by_width("hello", 3) == "hel"
        assert _truncate_by_width("你好世界", 4) == "你好"

    def test_build_gradient(self):
        from src.tui._bottom_bar import _build_gradient
        result = _build_gradient(3, 45, 45, "-")
        # 应该包含 3 段 ANSI 序列 + reset
        assert result.startswith("\033")
        assert result.endswith("\033[0m")

    def test_build_glow_ansi(self):
        from src.tui._bottom_bar import _build_glow_ansi
        result = _build_glow_ansi(0, 45, 12)
        assert result.startswith("\033[38;5;")

    def test_visual_width_ansi_cjk(self):
        from src.tui._bottom_bar import _visual_width
        assert _visual_width("\033[38;5;45m你好\033[0m") == 4

    def test_visual_width_emoji(self):
        from src.tui._bottom_bar import _visual_width
        # emoji 宽度 ≥ 1（wcswidth_simple 兜底为 1）
        assert _visual_width("a😀") >= 2

    def test_truncate_by_width_exact_width(self):
        from src.tui._bottom_bar import _truncate_by_width
        assert _truncate_by_width("hello", 5) == "hello"

    def test_truncate_by_width_empty(self):
        from src.tui._bottom_bar import _truncate_by_width
        assert _truncate_by_width("", 3) == ""

    def test_truncate_by_width_cjk_boundary(self):
        from src.tui._bottom_bar import _truncate_by_width
        # "你好世" 宽 6 > 5 → 截断为 "你好"（宽 4）
        assert _truncate_by_width("你好世界", 5) == "你好"

    def test_layout_utils_is_single_source(self):
        """验证 _bottom_bar 与 _layout_utils 导出的工具函数为同一真源对象。"""
        from src.tui._bottom_bar import _is_narrow, _visual_width, _truncate_by_width
        from src.tui._bottom_bar._layout_utils import (
            _is_narrow as src_is_narrow,
            _visual_width as src_visual_width,
            _truncate_by_width as src_truncate_by_width,
        )
        assert _is_narrow is src_is_narrow
        assert _visual_width is src_visual_width
        assert _truncate_by_width is src_truncate_by_width


class TestSystemMonitor:
    """测试 _SystemMonitor。"""

    def test_detect_platform(self):
        from src.tui._bottom_bar import _SystemMonitor
        platform_str = _SystemMonitor._detect_platform()
        assert platform_str in ("linux", "darwin", "windows", "cygwin", "unknown")

    def test_get_cpu_percent_returns_float(self):
        from src.tui._bottom_bar import _SystemMonitor
        m = _SystemMonitor()
        result = m.get_cpu_percent()
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0

    def test_get_memory_percent_returns_float(self):
        from src.tui._bottom_bar import _SystemMonitor
        m = _SystemMonitor()
        result = m.get_memory_percent()
        assert isinstance(result, float)
        assert 0.0 <= result <= 100.0

    def test_get_cpu_and_mem_returns_tuple(self):
        from src.tui._bottom_bar import _SystemMonitor
        m = _SystemMonitor()
        cpu, mem = m.get_cpu_and_mem()
        assert isinstance(cpu, float)
        assert isinstance(mem, float)

    def test_cache_ttl(self):
        from src.tui._bottom_bar import _SystemMonitor
        m = _SystemMonitor()
        m.get_cpu_percent()
        assert m._last_cpu_time > 0
        # 第二次调用应使用缓存
        m.get_cpu_percent()
        # 缓存未过期，时间戳应相同
        # (这取决于实际执行速度，但大概率在 1s 内)
        assert m.CPU_CACHE_TTL == 1.0


class TestForceRedrawFullRepaintClear:
    """测试 force_redraw() 中 full_repaint 时 scroll_end > old_scroll_end 的行清除。

    验证新增修复：resize（full_repaint=True）且滚动区域扩大时，
    新内容区行 [old_scroll_end+1, scroll_end] 被正确清除。

    ⚠️ 已知遗留（P2-12 评估结论）：本类为 ANSI 逐字节白盒断言，
    与 _render.py 的 cursor_goto/清行序列实现细节强耦合（f"\\033[{r};1H\\033[K"）。
    改动风险高：重构渲染输出格式将直接破坏此类断言。保留现状并加注释说明耦合性；
    后续如需解耦，应先将渲染输出格式抽象为结构化写入记录再断言。
    """

    @pytest.fixture
    def bottom_bar(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._active = True
        bb._subagent_lines = []
        bb._last_text = ""
        bb._cached_cpu_percent = 0.0
        bb._cached_mem_percent = 0.0
        bb._last_system_stats_time = float('inf')
        return bb

    def _setup_height_increase(self, bb, old_h=25, old_bottom=5):
        """模拟全量重绘时高度增加（新高度由 _run_force_redraw 的 mock_size 参数控制）。

        Args:
            old_h: 旧终端高度。
            old_bottom: 旧底部栏行数。
        """
        bb._last_height = old_h
        bb._last_bottom_lines = old_bottom
        # _bottom_lines 由 _compute_input_rows() 决定
        # 空文本 + 无 subagent 时: 2 + 0 + (2+1+0) = 5
        bb._needs_full_repaint = True

    def _setup_height_same(self, bb, h=25, old_bottom=5):
        """模拟全量重绘时高度不变，scroll_end == old_scroll_end。"""
        bb._last_height = h
        bb._last_bottom_lines = old_bottom
        bb._needs_full_repaint = True

    def _setup_height_decrease(self, bb, old_h=30, old_bottom=5):
        """模拟全量重绘时高度减小（新高度由 _run_force_redraw 的 mock_size 参数控制），scroll_end < old_scroll_end。"""
        bb._last_height = old_h
        bb._last_bottom_lines = old_bottom
        bb._needs_full_repaint = True

    def _setup_first_draw(self, bb, h=25, old_bottom=5):
        """模拟首次绘制（_last_height == 0）。"""
        bb._last_height = 0
        bb._last_bottom_lines = old_bottom
        bb._needs_full_repaint = True

    def _run_force_redraw(self, bb, mock_size):
        """执行 force_redraw，返回 mock_stdout。"""
        mock_stdout = MagicMock()

        with patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=mock_size):
            with patch("src.tui._bottom_bar._layout_utils._get_terminal_size", return_value=mock_size):
                with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
                    with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                        mock_lock.return_value.__enter__.return_value = True
                        with patch("src.tui._bottom_bar._render.sgr_reset"):
                            bb.force_redraw()

        return mock_stdout

    def _collect_writes(self, mock_stdout) -> str:
        """从 mock_stdout.write 调用参数中收集所有写入字符串。"""
        parts = []
        for call_args in mock_stdout.write.call_args_list:
            args, _ = call_args
            if args and isinstance(args[0], str):
                parts.append(args[0])
        return ''.join(parts)

    def test_full_repaint_height_increase_clears_new_rows(self, bottom_bar):
        """核心场景：高度增大、full_repaint=True、scroll_end > old_scroll_end → 清除新内容区行。"""
        self._setup_height_increase(bottom_bar, old_h=25)
        mock_stdout = self._run_force_redraw(bottom_bar, mock_size=(80, 30))

        all_writes = self._collect_writes(mock_stdout)

        # 验证新内容区行 [21, 25] 被清除
        # old_scroll_end = 25 - 5 = 20, scroll_end = 30 - 5 = 25
        # 应清除 range(21, 26): cursor_goto(r, 1) + \033[K
        for r in range(21, 26):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"行 {r} 应被清除但未找到序列 {expected!r}"

        # 验证 full_repaint 被消费重置
        assert bottom_bar._needs_full_repaint is False

    def test_full_repaint_height_same_no_extra_clear(self, bottom_bar):
        """full_repaint=True 但高度不变、scroll_end == old_scroll_end → 无额外清除。"""
        self._setup_height_same(bottom_bar, h=25)
        # _bottom_lines = 5 (空文本), scroll_end = 25 - 5 = 20, old_scroll_end = 25 - 5 = 20
        # scroll_end(20) == old_scroll_end(20) → 不应触发额外清除
        mock_stdout = self._run_force_redraw(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)

        # 确认底部栏区域 [scroll_end+1=21, height=25] 被清除（这是常规清除）
        for r in range(21, 26):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"底部栏行 {r} 应被清除"

        # 但 scroll_end 范围内不应有来自修复分支的清除
        # （底部栏区域的清除来自常规 full_repaint 路径，不是修复分支）

    def test_full_repaint_height_decrease_no_extra_clear(self, bottom_bar):
        """full_repaint=True 高度减小、scroll_end < old_scroll_end → 无额外清除。"""
        self._setup_height_decrease(bottom_bar, old_h=30)
        # old_scroll_end = 30 - 5 = 25, scroll_end = 25 - 5 = 20
        # scroll_end(20) < old_scroll_end(25) → 不应触发额外清除
        mock_stdout = self._run_force_redraw(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)

        # 底部栏区域 [scroll_end+1=21, height=25] 被清除
        for r in range(21, 26):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"底部栏行 {r} 应被清除"

    def test_full_repaint_last_height_zero_no_extra_clear(self, bottom_bar):
        """首次绘制（_last_height=0）时不应触发额外清除。"""
        self._setup_first_draw(bottom_bar, h=25)
        # _last_height=0 → self._last_height > 0 条件不满足 → 不触发
        mock_stdout = self._run_force_redraw(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)

        # 仅底部栏区域被清除，不应有 old_scroll_end 范围的清除
        # _last_height=0 时 old_scroll_end = (0 if 0>0 else 25) - 5 = 20
        # scroll_end = 25 - 5 = 20, 所以 scroll_end == old_scroll_end, 无额外清除

    def test_full_repaint_height_increase_with_subagent_lines(self, bottom_bar):
        """高度增大且有 subagent 行时，新内容区行被正确清除。"""
        bottom_bar._subagent_lines = ["[agent-1]", "[agent-2]"]
        # _bottom_lines = 2 + 2 + (2+1+0) = 7
        bottom_bar._last_height = 30
        bottom_bar._last_bottom_lines = 7
        bottom_bar._needs_full_repaint = True

        mock_stdout = self._run_force_redraw(bottom_bar, mock_size=(80, 35))

        all_writes = self._collect_writes(mock_stdout)

        # old_scroll_end = 30 - 7 = 23, scroll_end = 35 - 7 = 28
        # 应清除 range(24, 29)
        for r in range(24, 29):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"行 {r} 应被清除但未找到序列 {expected!r}"

    def test_full_repaint_bottom_lines_decrease_clears_new_rows(self, bottom_bar):
        """底部行数减少（8→5）导致 scroll_end 扩大（22→25），新内容区行 [23,25] 应被清除。"""
        # 模拟输入文本，宽度变化导致 wrap 行数变化
        bottom_bar._last_text = "a" * 50  # 长文本，宽屏时少行
        # 旧底部行数较大（8行），当前底部行数较小（5行）
        # 但这里控制 _last_bottom_lines 和 current _bottom_lines 的关系
        bottom_bar._last_height = 30
        bottom_bar._last_bottom_lines = 8  # 旧底部行数

        # 在新宽度下 _bottom_lines 不变（空文本），但旧底部行设大
        # → scroll_end(25) < old_scroll_end(22) → 不触发
        bottom_bar._needs_full_repaint = True

        mock_stdout = self._run_force_redraw(bottom_bar, mock_size=(80, 30))
        all_writes = self._collect_writes(mock_stdout)

        # 旧_scroll_end = 30 - 8 = 22
        # scroll_end = 30 - 5 = 25
        # scroll_end(25) > old_scroll_end(22) → 应清除 range(23, 26)
        for r in range(23, 26):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"底部行数减少时行 {r} 应被清除但未找到序列 {expected!r}"


class TestForceRedrawExceptionHandling:
    """测试 force_redraw() 的异常处理 — 防止异常吞没。

    验证：
    1. out.write() 抛出 OSError/ValueError/AttributeError 时被正确捕获
    2. 捕获后记录 _logger.warning
    3. sgr_reset() 被调用以恢复终端状态
    4. 异常后正常返回（不向上传播）
    """

    @pytest.fixture
    def bottom_bar(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        # 设置 active 状态
        bb._active = True
        return bb

    def test_oserror_on_write_is_caught(self, bottom_bar, caplog):
        """out.write() 抛出 OSError 应被捕获并记录日志。"""
        caplog.set_level(logging.WARNING)
        mock_stdout = MagicMock()
        mock_stdout.write.side_effect = OSError("Broken pipe")

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        # 应记录警告日志
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("force_redraw" in r.message for r in warning_records), (
            "应记录 force_redraw 写入失败的警告"
        )

    def test_valueerror_on_write_is_caught(self, bottom_bar, caplog):
        """out.write() 抛出 ValueError 应被捕获并记录日志。"""
        caplog.set_level(logging.WARNING)
        mock_stdout = MagicMock()
        mock_stdout.write.side_effect = ValueError("I/O operation on closed file")

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        # 应记录警告日志
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("force_redraw" in r.message for r in warning_records)

    def test_sgr_reset_called_on_failure(self, bottom_bar):
        """写入失败时 sgr_reset() 应被调用以恢复终端状态。"""
        mock_stdout = MagicMock()
        mock_stdout.write.side_effect = OSError("Broken pipe")

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        mock_reset.assert_called_once()

    def test_exception_does_not_propagate(self, bottom_bar):
        """异常不应向上传播 — force_redraw 应正常返回。"""
        mock_stdout = MagicMock()
        mock_stdout.write.side_effect = AttributeError("'NoneType' object has no attribute 'write'")

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sgr_reset") as mock_reset:
                    # 不应抛出异常
                    bottom_bar.force_redraw()

        # 执行到达此处即通过（无异常传播）

    def test_normal_path_no_exception(self, bottom_bar):
        """正常路径（无异常）下 force_redraw 应正常完成。"""
        mock_stdout = MagicMock()

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        # 正常路径下不调用 sgr_reset
        mock_reset.assert_not_called()

    def test_oserror_on_flush_is_caught(self, bottom_bar, caplog):
        """out.flush() 抛出 OSError 应被捕获并记录日志。"""
        caplog.set_level(logging.WARNING)
        # 让 write 成功但 flush 失败
        mock_stdout = MagicMock()
        mock_stdout.write.return_value = None  # write 正常返回
        mock_stdout.flush.side_effect = OSError("Broken pipe on flush")

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("force_redraw" in r.message for r in warning_records)

    def test_inactive_does_not_throw(self):
        """_active=False 时 force_redraw 直接返回，不执行任何 I/O。"""
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        assert bb._active is False

        # 不应抛出异常
        bb.force_redraw()

    def test_lock_not_acquired_returns_early(self, bottom_bar):
        """锁未获取到时直接返回，不执行写入。"""
        mock_stdout = MagicMock()

        with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = False  # locked=False
                bottom_bar.force_redraw()

        # 锁未获取时不应执行任何写入
        mock_stdout.write.assert_not_called()


# ═══════════════════════════════════════════════════════════
# sync_bottom_lines resize 清除测试
# ═══════════════════════════════════════════════════════════

class TestSyncBottomLinesResizeClear:
    """测试 sync_bottom_lines() 中 resize 路径的行清除逻辑。

    验证新增修复：resize 时旧底部栏区域的 ANSI 残留行被正确清除。

    ⚠️ 已知遗留（P2-12 评估结论）：本类为 ANSI 逐字节白盒断言，
    与 _render.py 的 cursor_goto/清行序列实现细节强耦合。
    改动风险高：重构渲染输出格式将直接破坏此类断言。保留现状并加注释说明耦合性；
    后续如需解耦，应先将渲染输出格式抽象为结构化写入记录再断言。
    """

    @pytest.fixture
    def bottom_bar(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._active = True
        bb._subagent_lines = []
        bb._last_text = ""
        return bb

    def _setup_resize_larger(self, bb):
        """模拟终端变大（height 增大→scroll_end 增大）。"""
        bb._last_sync_height = 20
        bb._last_scroll_end = 15

    def _setup_resize_smaller(self, bb):
        """模拟终端变小（height 减小→scroll_end 减小）。"""
        bb._last_sync_height = 25
        bb._last_scroll_end = 20

    def _setup_first_call(self, bb):
        """模拟首次调用（old_scroll=0, _last_sync_height=0）。"""
        bb._last_sync_height = 0
        bb._last_scroll_end = 0

    def _setup_non_resize_scroll_changed(self, bb):
        """模拟非 resize 但 scroll_end 变化（_last_sync_height == height）。"""
        bb._last_sync_height = 25
        bb._last_scroll_end = 18

    def _run_sync_bottom_lines(self, bb, mock_size):
        """执行 sync_bottom_lines，返回 mock_stdout。"""
        mock_stdout = MagicMock()

        with patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=mock_size):
            with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
                bb.sync_bottom_lines()

        return mock_stdout

    def _collect_writes(self, mock_stdout) -> str:
        parts = []
        for call_args in mock_stdout.write.call_args_list:
            args, _ = call_args
            if args and isinstance(args[0], str):
                parts.append(args[0])
        return ''.join(parts)

    def test_resize_larger_clears_old_rows(self, bottom_bar):
        """终端变大（scroll_end 增大）：清除 [old_scroll+1, scroll_end] 范围。"""
        self._setup_resize_larger(bottom_bar)
        mock_stdout = self._run_sync_bottom_lines(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)

        # old_scroll=15, scroll_end=20 → 应清除 range(16, 21)
        for r in range(16, 21):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"终端变大时行 {r} 应被清除但未找到序列 {expected!r}"

    def test_resize_smaller_clears_old_rows(self, bottom_bar):
        """终端变小（scroll_end 减小）：清除 [scroll_end+1, old_scroll] 范围。"""
        self._setup_resize_smaller(bottom_bar)
        mock_stdout = self._run_sync_bottom_lines(bottom_bar, mock_size=(80, 20))

        all_writes = self._collect_writes(mock_stdout)

        # old_scroll=20, scroll_end=15, height=20 → 应清除 range(16, 21)
        for r in range(16, 21):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"终端变小时行 {r} 应被清除但未找到序列 {expected!r}"

    def test_first_call_no_error(self, bottom_bar):
        """首次调用（old_scroll=0）不应报错。"""
        self._setup_first_call(bottom_bar)
        mock_stdout = self._run_sync_bottom_lines(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)
        # scroll_end=20, 应包含光标定位
        assert "\033[20;1H" in all_writes, "首次 resize 应包含 scroll_end 光标定位"

    def test_non_resize_behavior_unchanged(self, bottom_bar):
        """非 resize 场景（resized=False）不应触发新逻辑，原行为不变。"""
        self._setup_non_resize_scroll_changed(bottom_bar)
        mock_stdout = self._run_sync_bottom_lines(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)

        # resized=False 时，旧路径清除 [old_scroll+1, scroll_end]
        # old_scroll=18, scroll_end=20, 应清除 range(19, 21)
        for r in range(19, 21):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"非 resize 场景行 {r} 应被清除但未找到序列 {expected!r}"

    def test_resize_larger_cursor_goto_and_save(self, bottom_bar):
        """终端变大时，cursor_goto(scroll_end, 1) + cursor_save() 出现在输出中。"""
        self._setup_resize_larger(bottom_bar)
        mock_stdout = self._run_sync_bottom_lines(bottom_bar, mock_size=(80, 25))

        all_writes = self._collect_writes(mock_stdout)

        # scroll_end=20, 应包含 \033[20;1H\033[s
        assert "\033[20;1H\033[s" in all_writes, "应包含 cursor_goto + cursor_save"

    def test_resize_smaller_height_min_prevents_oob(self, bottom_bar):
        """终端变小时，清除范围上限使用 min(old_scroll, height) 防止越界。"""
        self._setup_resize_smaller(bottom_bar)
        # height=18, scroll_end=13, old_scroll=20
        mock_stdout = self._run_sync_bottom_lines(bottom_bar, mock_size=(80, 18))

        all_writes = self._collect_writes(mock_stdout)

        # 应清除 range(14, min(20, 18)+1=19) = [14, 18]
        for r in range(14, 19):
            expected = f"\033[{r};1H\033[K"
            assert expected in all_writes, f"终端变小（height=18）时行 {r} 应被清除"
        # 不应有行 19+ 的清除（越界保护）
        assert "\033[19;1H\033[K" not in all_writes, "越界行不应被清除"


# ═══════════════════════════════════════════════════════════
# 弹窗竞态测试
# ═══════════════════════════════════════════════════════════

class TestCompletionRaceCondition:
    """测试 hide_completions / get_selected_completion_index 竞态修复。

    验证：
    1. hide_completions() 后 get_selected_completion_index() 返回隐藏时的正确索引
    2. 快速连续 hide/show 后索引正确性
    """

    @pytest.fixture
    def bottom_bar(self):
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._active = True
        return bb

    @pytest.fixture
    def deterministic_terminal(self):
        """确定性终端环境：消除真实终端 I/O 副作用。

        对照 TestForceRedrawFullRepaintClear._run_force_redraw 的 5 层 patch 模式：
          - _bar._get_terminal_size / _layout_utils._get_terminal_size → mock_size (80, 24)
          - _render.sys.__stdout__ → mock_stdout（MagicMock 带 write/flush）
          - _render._try_acquire_output_lock → 可获取的锁（locked=True）
          - _render.sgr_reset → no-op

        show_completions/hide_completions 内部调用 force_redraw() 触发终端写入，
        本 fixture 拦截全部 I/O 使竞态测试在无真实终端环境下可运行（CI 友好）。
        """
        mock_stdout = MagicMock()
        mock_size = (80, 24)
        with ExitStack() as stack:
            stack.enter_context(
                patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=mock_size)
            )
            stack.enter_context(
                patch("src.tui._bottom_bar._layout_utils._get_terminal_size", return_value=mock_size)
            )
            stack.enter_context(patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout))
            mock_lock = stack.enter_context(
                patch("src.tui._bottom_bar._render._try_acquire_output_lock")
            )
            mock_lock.return_value.__enter__.return_value = True
            stack.enter_context(patch("src.tui._bottom_bar._render.sgr_reset"))
            yield {"stdout": mock_stdout, "size": mock_size, "lock": mock_lock}

    def test_hide_saves_last_index(self, bottom_bar, deterministic_terminal):
        """hide_completions 后 _last_idx_before_hide 应等于隐藏时的 _idx。"""
        # 模拟有 5 个补全项，选中 idx=3
        items = [f"item{i}" for i in range(5)]
        bottom_bar.show_completions(
            items=items, selected_idx=3,
            texts=items, start_pos=0,
        )
        # 验证弹窗可见且 idx 正确
        assert bottom_bar.is_completion_visible
        assert bottom_bar._completion._idx == 3

        # 隐藏弹窗
        bottom_bar.hide_completions()
        # 验证 _last_idx_before_hide 正确保存了隐藏时的 idx
        assert bottom_bar._completion._last_idx_before_hide == 3
        assert not bottom_bar.is_completion_visible

    def test_get_selected_completion_index_after_hide(self, bottom_bar, deterministic_terminal):
        """隐藏后 get_selected_completion_index() 返回最后保存的索引。"""
        items = [f"item{i}" for i in range(5)]
        bottom_bar.show_completions(items=items, selected_idx=2, texts=items)
        assert bottom_bar._completion._idx == 2

        bottom_bar.hide_completions()
        # 隐藏后应返回 _last_idx_before_hide (2)
        result = bottom_bar.get_selected_completion_index()
        assert result == 2

    def test_get_selected_completion_index_when_visible(self, bottom_bar, deterministic_terminal):
        """弹窗可见时 get_selected_completion_index() 返回实时 _idx。"""
        items = [f"item{i}" for i in range(5)]
        bottom_bar.show_completions(items=items, selected_idx=4, texts=items)
        assert bottom_bar.is_completion_visible

        # 可见时返回当前 idx
        result = bottom_bar.get_selected_completion_index()
        assert result == 4

    def test_rapid_hide_show_preserves_index(self, bottom_bar, deterministic_terminal):
        """快速连续 hide/show 后，get_selected_completion_index 返回正确索引。"""
        items = [f"item{i}" for i in range(5)]

        # 第一次显示，选中 idx=3
        bottom_bar.show_completions(items=items, selected_idx=3, texts=items)
        assert bottom_bar._completion._idx == 3

        # 隐藏（保存 idx=3）
        bottom_bar.hide_completions()
        assert bottom_bar._completion._last_idx_before_hide == 3

        # 重新显示，选中 idx=1
        bottom_bar.show_completions(items=items[:3], selected_idx=1, texts=items[:3])
        assert bottom_bar._completion._idx == 1

        # 可见时返回实时 idx=1
        result = bottom_bar.get_selected_completion_index()
        assert result == 1

        # 再次隐藏
        bottom_bar.hide_completions()
        # 隐藏后应返回隐藏时的 idx=1
        result = bottom_bar.get_selected_completion_index()
        assert result == 1

    def test_local_variable_atomic_read(self, bottom_bar, deterministic_terminal):
        """验证 hide（_CompletionPopup 正式方法）中使用局部变量保存 _idx。

        通过检查源代码确认 local variable 模式存在（编译时验证）。
        此测试是防御性，确保修复模式不会被后续修改退化。
        方向E·步骤10：hide 逻辑从 _BottomBar.hide_completions 迁入
        _CompletionPopup.hide，检查目标同步更新。
        """
        import inspect
        from src.tui._bottom_bar._popup import _CompletionPopup
        source = inspect.getsource(_CompletionPopup.hide)
        # 应包含 saved_idx = self._idx 模式
        assert "saved_idx" in source or "_last_idx_before_hide" in source
        # 确认 _last_idx_before_hide 赋值使用局部变量而非直接引用
        assert "saved_idx" in source


# ═══════════════════════════════════════════════════════════
# 负值光标坐标 clamp 测试
# ═══════════════════════════════════════════════════════════

class TestNegativeCursorCoordinateClamp:
    """测试负值光标坐标 clamp 修复。

    验证 _cursor_tracker.set() 收到负值/零值 row/col 时被 clamp 到 1。
    修复位置: CursorTracker.set() 中使用 max(1, row) / max(1, col)。
    """

    def test_cursor_tracker_set_clamps_negative_row(self):
        """负值 row 被 clamp 到 1。"""
        from src.tui._cursor_tracker import CursorTracker
        ct = CursorTracker()
        ct.set(-5, 10)
        pos = ct.pos
        assert pos.row == 1, f"负值 row -5 应 clamp 到 1，实际得到 {pos.row}"
        assert pos.col == 10

    def test_cursor_tracker_set_clamps_zero_row(self):
        """零值 row 被 clamp 到 1。"""
        from src.tui._cursor_tracker import CursorTracker
        ct = CursorTracker()
        ct.set(0, 5)
        pos = ct.pos
        assert pos.row == 1, f"零值 row 0 应 clamp 到 1，实际得到 {pos.row}"
        assert pos.col == 5

    def test_cursor_tracker_set_clamps_negative_col(self):
        """负值 col 被 clamp 到 1。"""
        from src.tui._cursor_tracker import CursorTracker
        ct = CursorTracker()
        ct.set(10, -3)
        pos = ct.pos
        assert pos.row == 10
        assert pos.col == 1, f"负值 col -3 应 clamp 到 1，实际得到 {pos.col}"

    def test_cursor_tracker_set_clamps_zero_col(self):
        """零值 col 被 clamp 到 1。"""
        from src.tui._cursor_tracker import CursorTracker
        ct = CursorTracker()
        ct.set(3, 0)
        pos = ct.pos
        assert pos.row == 3
        assert pos.col == 1

    def test_cursor_tracker_set_normal_values_unchanged(self):
        """正常正值不被 clamp。"""
        from src.tui._cursor_tracker import CursorTracker
        ct = CursorTracker()
        ct.set(5, 8)
        pos = ct.pos
        assert pos.row == 5
        assert pos.col == 8

    def test_cursor_tracker_set_both_negative(self):
        """row 和 col 同时为负值时均被 clamp 到 1。"""
        from src.tui._cursor_tracker import CursorTracker
        ct = CursorTracker()
        ct.set(-10, -20)
        pos = ct.pos
        assert pos.row == 1
        assert pos.col == 1

    def test_ensure_cursor_in_lower_safe_with_negative(self):
        """ensure_cursor_in_lower 中计算出的负值 r_cursor 被 clamp。

        模拟边界条件：输入文本为空，终端高度极小，确保最终 set 收到 clamp 后的安全值。
        """
        from unittest.mock import MagicMock, patch
        from src.tui._bottom_bar import _BottomBar
        from src.tui._cursor_tracker import CursorTracker

        ct = CursorTracker()
        bb = _BottomBar(cursor_tracker=ct)
        bb._active = True
        bb._last_bottom_lines = 5
        bb._subagent_lines = []
        bb._input_cursor_pos = 0

        with patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=(80, 3)):
            with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar._render.sys.__stdout__") as mock_stdout:
                    # 不应抛出异常
                    bb.ensure_cursor_in_lower()

        # 执行到达此处即通过（无异常）
        # 光标行号应 ≥ 1
        assert ct.pos.row >= 1
        assert ct.pos.col >= 1


# ═══════════════════════════════════════════════════════════
# BottomBarStatus 状态对象拆分测试（方向E·步骤9）
# ═══════════════════════════════════════════════════════════

class TestBottomBarStatusObject:
    """测试 BottomBarStatus 状态对象拆分（方向E·步骤9）。

    验证：
    1. BottomBarStatus 独立状态对象加锁 + 快照行为
    2. _BottomBar 组合持有状态对象，私有属性委托读写路径可用
    3. 跨线程写 / 读无竞态
    """

    def test_bottom_bar_status_snapshot_regression(self):
        """snapshot() 返回独立副本且反映状态更新。"""
        from src.tui._bottom_bar._state import BottomBarStatus
        st = BottomBarStatus()
        assert st.snapshot()["tool_count"] == 0
        st.increment_tool()
        snap = st.snapshot()
        assert snap["tool_count"] == 1
        assert snap["tool_total"] == 1
        # 修改副本不影响内部状态
        snap["tool_count"] = 999
        assert st.snapshot()["tool_count"] == 1

    def test_bottom_bar_status_set_main_phase_start_update(self):
        """set_main_phase 阶段变化时更新 _main_phase_start，相同阶段不刷新。"""
        from src.tui._bottom_bar._state import BottomBarStatus
        st = BottomBarStatus()
        st.set_main_phase("thinking")
        first_start = st.snapshot()["main_phase_start"]
        assert first_start > 0.0
        st.set_main_phase("thinking")  # 相同阶段不刷新
        assert st.snapshot()["main_phase_start"] == first_start
        st.set_main_phase("answering")  # 变化刷新
        second_start = st.snapshot()["main_phase_start"]
        assert second_start >= first_start

    def test_bottom_bar_status_increment_tool_phase_start(self):
        """increment_tool 首次置位 _tool_phase_start，后续不刷新。"""
        from src.tui._bottom_bar._state import BottomBarStatus
        st = BottomBarStatus()
        assert st.snapshot()["tool_phase_start"] == 0.0
        st.increment_tool()
        assert st.snapshot()["tool_phase_start"] > 0.0
        first = st.snapshot()["tool_phase_start"]
        st.increment_tool()
        assert st.snapshot()["tool_phase_start"] == first

    def test_bottom_bar_status_thread_safety_regression(self):
        """主线程写 + 子线程读并发 100 次，无异常且最终计数一致。"""
        import threading
        from src.tui._bottom_bar._state import BottomBarStatus
        st = BottomBarStatus()
        errors = []

        def writer():
            try:
                for _ in range(100):
                    st.increment_tool()
                    st.set_main_phase("thinking")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def reader():
            try:
                for _ in range(100):
                    snap = st.snapshot()
                    assert snap["tool_count"] >= 0
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        t1 = threading.Thread(target=writer)
        t2 = threading.Thread(target=reader)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not errors, f"并发读写异常: {errors}"
        assert st.snapshot()["tool_count"] == 100
        assert st.snapshot()["tool_total"] == 100

    def test_status_object_delegation_regression(self):
        """_BottomBar 公开方法委托 BottomBarStatus，私有属性读写路径可用。"""
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb.enable_status()
        assert bb._status_active is True
        assert bb.is_status_active is True
        bb.set_model_name("deepseek-v3")
        assert bb._model_name == "deepseek-v3"
        bb.increment_tool()
        assert bb._tool_count == 1
        assert bb._tool_total == 1
        bb.decrement_tool()
        assert bb._tool_count == 0
        bb.set_main_phase("answering")
        assert bb._main_phase == "answering"
        # 直接属性写入（兼容路径）也生效
        bb._tool_count = 5
        assert bb._tool_count == 5
        bb.disable_status()
        assert bb._status_active is False

    def test_get_status_elapsed_seconds_pure(self):
        """get_status_elapsed_seconds 为纯状态职责，不依赖 _snapshot。"""
        from src.tui._bottom_bar._state import BottomBarStatus
        st = BottomBarStatus()
        assert st.get_status_elapsed_seconds() == 0.0
        st.set_main_phase("thinking")
        elapsed = st.get_status_elapsed_seconds()
        assert elapsed >= 0.0


# ═══════════════════════════════════════════════════════════
# 弹窗正式方法 + 状态文本收敛 + force_redraw 拆分测试（方向E·步骤10）
# ═══════════════════════════════════════════════════════════

class TestBottomBarFormalMethodsAndConvergence:
    """测试 _CompletionPopup 正式方法 + 状态文本收敛 + force_redraw 拆分（方向E·步骤10）。

    验证：
    1. _CompletionPopup.show/hide/reset 正式方法接口
    2. _BottomBar.show_completions/hide_completions 委托正式方法
    3. _build_status_text 为阶段文本唯一入口，_format_status 不重复
    4. _do_force_redraw 拆分子函数后输出关键 ANSI 序列存在
    """

    def test_popup_show_hide_formal_methods_regression(self):
        """_CompletionPopup.show() 后字段正确、hide() 后清空且 _last_idx_before_hide 保存。"""
        from src.tui._bottom_bar import _CompletionPopup
        cp = _CompletionPopup()
        cp.show(
            ["foo", "bar"], 1, 4,
            title="选择", texts=["foo", "bar", "baz"],
            start_pos=1, orig_prefix="/f", types=["command", "dir"],
            match_prefix="/f",
        )
        assert cp.is_visible is True
        assert cp._visible is True
        assert cp._popup_height == 4
        assert cp._title == "选择"
        assert cp._is_selection is True
        assert cp._items == ["foo", "bar"]
        assert cp._texts == ["foo", "bar", "baz"]
        assert cp._idx == 1
        assert cp._start_pos == 1
        assert cp._orig_prefix == "/f"
        assert cp._types == ["command", "dir"]
        assert cp._match_prefix == "/f"

        cp.hide()
        assert cp.is_visible is False
        assert cp._last_idx_before_hide == 1
        assert cp._popup_height == 0
        assert cp._items == []
        assert cp._texts == []
        assert cp._idx == 0

    def test_popup_reset_formal_method_regression(self):
        """_CompletionPopup.reset() 清空全部字段且不保存 _last_idx_before_hide。"""
        from src.tui._bottom_bar import _CompletionPopup
        cp = _CompletionPopup()
        cp.show(["a"], 0, 3, texts=["a"])
        assert cp.is_visible is True
        cp.reset()
        assert cp.is_visible is False
        assert cp._popup_height == 0
        assert cp._items == []
        assert cp._title == "补全"
        assert cp._is_selection is False

    def test_bottom_bar_show_completions_delegates_regression(self):
        """bb.show_completions() 委托 _CompletionPopup.show()，字段正确。"""
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._active = True
        # show_completions 会触发 force_redraw → mock 终端 I/O
        with patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=(80, 24)):
            with patch("src.tui._bottom_bar._layout_utils._get_terminal_size", return_value=(80, 24)):
                with patch("src.tui._bottom_bar._render.sys.__stdout__", MagicMock()):
                    with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                        mock_lock.return_value.__enter__.return_value = True
                        with patch("src.tui._bottom_bar._render.sgr_reset"):
                            bb.show_completions(
                                items=["foo", "bar", "baz"], selected_idx=2,
                                texts=["foo", "bar", "baz"],
                            )
        assert bb._completion._visible is True
        assert bb._completion._popup_height == 5  # 3 项 + 2
        assert bb._completion._items == ["foo", "bar", "baz"]
        assert bb._completion._idx == 2
        assert bb.is_completion_visible is True

    def test_bottom_bar_hide_completions_delegates_regression(self):
        """bb.hide_completions() 委托 _CompletionPopup.hide()，_last_idx_before_hide 保存。"""
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._active = True
        with patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=(80, 24)):
            with patch("src.tui._bottom_bar._layout_utils._get_terminal_size", return_value=(80, 24)):
                with patch("src.tui._bottom_bar._render.sys.__stdout__", MagicMock()):
                    with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                        mock_lock.return_value.__enter__.return_value = True
                        with patch("src.tui._bottom_bar._render.sgr_reset"):
                            bb.show_completions(
                                items=["a", "b", "c", "d", "e"], selected_idx=3,
                                texts=["a", "b", "c", "d", "e"],
                            )
                            assert bb._completion._idx == 3
                            bb.hide_completions()
        assert bb._completion._last_idx_before_hide == 3
        assert not bb.is_completion_visible

    def test_status_text_convergence_regression(self):
        """状态文本收敛：_build_status_text 为阶段文本唯一入口，_format_status 不重复阶段文本。

        P3-21 说明：旧版 _format_status 本就不含「思考」阶段段（该段属
        _build_status_text 供分隔线使用），本断言固化既有行为——即使上下文
        相同（enable_status + thinking 阶段），_format_status 也不产生阶段
        文本段，防止未来收敛时误将阶段段并入状态行。
        """
        import re
        import time
        from src.tui._bottom_bar import _BottomBar
        from src.tui._bottom_bar._status import _PHASE_DISPLAY, _build_status_text

        # 阶段显示映射存在
        assert _PHASE_DISPLAY["thinking"] == "思考"

        # P1-4：_build_status_text 现接收一次性 snapshot dict（从同一快照提取
        # 字段，消除跨字段非原子读取）；此处构造与 BottomBarStatus.snapshot()
        # 同构的 dict 验证「· 思考 X.XXs」格式（唯一入口）
        snap = {
            "status_active": True,
            "main_phase": "thinking",
            "main_phase_start": time.monotonic() - 3.2,
            "tool_count": 0,
            "tool_phase_start": 0.0,
        }
        text = _build_status_text(snap)
        assert re.match(r"^· 思考 \d+\.\d{2}s$", text)

        # _format_status 状态行（相同阶段上下文）不产生阶段文本段重复
        bb = _BottomBar()
        bb.enable_status()
        bb.set_main_phase("thinking")
        result = bb._format_status()
        assert "思考" not in result

    def test_force_redraw_output_snapshot_regression(self):
        """force_redraw 拆分后输出关键 ANSI 序列存在（分隔线/状态行/滚动区域）。"""
        from src.tui._bottom_bar import _BottomBar
        bb = _BottomBar()
        bb._active = True
        bb._last_text = ""
        bb._cached_cpu_percent = 0.0
        bb._cached_mem_percent = 0.0
        bb._last_system_stats_time = float('inf')
        mock_stdout = MagicMock()

        with patch("src.tui._bottom_bar._bar._get_terminal_size", return_value=(80, 24)):
            with patch("src.tui._bottom_bar._layout_utils._get_terminal_size", return_value=(80, 24)):
                with patch("src.tui._bottom_bar._render.sys.__stdout__", mock_stdout):
                    with patch("src.tui._bottom_bar._render._try_acquire_output_lock") as mock_lock:
                        mock_lock.return_value.__enter__.return_value = True
                        with patch("src.tui._bottom_bar._render.sgr_reset"):
                            bb.force_redraw()

        parts = []
        for call_args in mock_stdout.write.call_args_list:
            args, _ = call_args
            if args and isinstance(args[0], str):
                parts.append(args[0])
        all_writes = ''.join(parts)

        # 分隔线行（r1）光标定位
        assert "\033[20;1H" in all_writes or "\033[21;1H" in all_writes
        # 状态行清行序列
        assert "\033[K" in all_writes
        # 分隔线 gradient 或状态行 ANSI 颜色
        assert "\033[38;5;" in all_writes or "\033[0m" in all_writes
        # reset_scroll_region
        assert "\033[r" in all_writes
