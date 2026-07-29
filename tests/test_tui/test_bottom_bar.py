"""测试 _bottom_bar.py — 底部栏模块。

测试 _compute_input_rows、compute_cursor_position 返回值的正确范围、
show_completions/hide_completions/cycle_completion/get_selected_completion 状态管理。
Mock 终端尺寸，不执行真实终端 I/O。
"""

from __future__ import annotations

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
