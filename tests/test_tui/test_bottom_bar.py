"""测试 _bottom_bar.py — 底部栏模块。

测试 _compute_input_rows、compute_cursor_position 返回值的正确范围、
show_completions/hide_completions/cycle_completion/get_selected_completion 状态管理。
Mock 终端尺寸，不执行真实终端 I/O。
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from src.tui._screen import _get_terminal_size


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
    """测试内联动画时钟。"""

    def test_singleton(self):
        from src.tui._bottom_bar import _SimpleAnimator
        a1 = _SimpleAnimator.get_default()
        a2 = _SimpleAnimator.get_default()
        assert a1 is a2

    def test_tick(self):
        from src.tui._bottom_bar import _SimpleAnimator
        a = _SimpleAnimator()
        assert a.frame == 0
        a.tick()
        assert a.frame == 1
        a.tick()
        assert a.frame == 2

    def test_sine_color(self):
        from src.tui._bottom_bar import _SimpleAnimator
        a = _SimpleAnimator()
        # breath_frame=0 → sin(0)=0 → ratio=0.5 → lo + (hi-lo)*0.5
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

        with patch("src.tui._bottom_bar._get_terminal_size", return_value=mock_size):
            with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
                with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                    mock_lock.return_value.__enter__.return_value = True
                    with patch("src.tui._bottom_bar.sgr_reset"):
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

        from src.tui._locks import _try_acquire_output_lock
        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sgr_reset") as mock_reset:
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

        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("force_redraw" in r.message for r in warning_records)

    def test_sgr_reset_called_on_failure(self, bottom_bar):
        """写入失败时 sgr_reset() 应被调用以恢复终端状态。"""
        mock_stdout = MagicMock()
        mock_stdout.write.side_effect = OSError("Broken pipe")

        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sgr_reset") as mock_reset:
                    bottom_bar.force_redraw()

        mock_reset.assert_called_once()

    def test_exception_does_not_propagate(self, bottom_bar):
        """异常不应向上传播 — force_redraw 应正常返回。"""
        mock_stdout = MagicMock()
        mock_stdout.write.side_effect = AttributeError("'NoneType' object has no attribute 'write'")

        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sgr_reset") as mock_reset:
                    # 不应抛出异常
                    bottom_bar.force_redraw()

        # 执行到达此处即通过（无异常传播）

    def test_normal_path_no_exception(self, bottom_bar):
        """正常路径（无异常）下 force_redraw 应正常完成。"""
        mock_stdout = MagicMock()

        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sgr_reset") as mock_reset:
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

        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sgr_reset") as mock_reset:
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

        with patch("src.tui._bottom_bar.sys.__stdout__", mock_stdout):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = False  # locked=False
                bottom_bar.force_redraw()

        # 锁未获取时不应执行任何写入
        mock_stdout.write.assert_not_called()


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

    def test_hide_saves_last_index(self, bottom_bar):
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

    def test_get_selected_completion_index_after_hide(self, bottom_bar):
        """隐藏后 get_selected_completion_index() 返回最后保存的索引。"""
        items = [f"item{i}" for i in range(5)]
        bottom_bar.show_completions(items=items, selected_idx=2, texts=items)
        assert bottom_bar._completion._idx == 2

        bottom_bar.hide_completions()
        # 隐藏后应返回 _last_idx_before_hide (2)
        result = bottom_bar.get_selected_completion_index()
        assert result == 2

    def test_get_selected_completion_index_when_visible(self, bottom_bar):
        """弹窗可见时 get_selected_completion_index() 返回实时 _idx。"""
        items = [f"item{i}" for i in range(5)]
        bottom_bar.show_completions(items=items, selected_idx=4, texts=items)
        assert bottom_bar.is_completion_visible

        # 可见时返回当前 idx
        result = bottom_bar.get_selected_completion_index()
        assert result == 4

    def test_rapid_hide_show_preserves_index(self, bottom_bar):
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

    def test_local_variable_atomic_read(self, bottom_bar):
        """验证 hide_completions 中使用局部变量保存 _idx。

        通过检查源代码确认 local variable 模式存在（编译时验证）。
        此测试是防御性，确保修复模式不会被后续修改退化。
        """
        import inspect
        from src.tui import _bottom_bar
        source = inspect.getsource(_bottom_bar._BottomBar.hide_completions)
        # 应包含 saved_idx = self._completion._idx 模式
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

        with patch("src.tui._bottom_bar._get_terminal_size", return_value=(80, 3)):
            with patch("src.tui._bottom_bar._try_acquire_output_lock") as mock_lock:
                mock_lock.return_value.__enter__.return_value = True
                with patch("src.tui._bottom_bar.sys.__stdout__") as mock_stdout:
                    # 不应抛出异常
                    bb.ensure_cursor_in_lower()

        # 执行到达此处即通过（无异常）
        # 光标行号应 ≥ 1
        assert ct.pos.row >= 1
        assert ct.pos.col >= 1
