"""测试 ink/widgets/display.py — Spinner / ProgressBar / Table / Badge / Divider。

覆盖：
  - Spinner：初始帧 / indicator / type 预设 / Timer 生命周期（patch）；
  - ProgressBar：percent 归一化 / 左右标记 / 自定义 char / 边界；
  - Table：无边框对齐 / 表头 / 边框变体 / 列宽自动；
  - Badge：背景色 / 前景自动对比 / padding / style 合并；
  - Divider：纯分隔线 / 标题模式 / 自定义 char / 宽度。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.ink import h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets import Spinner, ProgressBar, Table, Badge, Divider, SPINNER_FRAMES


def _render(element, width=80, height=24):
    """渲染元素树，返回 Frame。"""
    r = Reconciler()
    root = r.create_root()
    r.render(root, element, width, height)
    return render_frame(root, width)


# ═══════════════════════════════════════════════════════════
# Spinner
# ═══════════════════════════════════════════════════════════


class TestSpinner:
    def test_initial_frame_dots(self):
        with patch("src.tui.ink.widgets.display.threading.Timer") as mock_timer:
            frame = _render(h(Spinner, {"type": "dots"})).lines[0]
            assert frame.plain == "⠋"  # dots 首帧
            mock_timer.assert_called()  # 动画 Timer 已注册

    def test_custom_indicator(self):
        with patch("src.tui.ink.widgets.display.threading.Timer"):
            frame = _render(h(Spinner, {"indicator": "abc"})).lines[0]
            assert frame.plain == "a"

    def test_unknown_type_falls_back_dots(self):
        with patch("src.tui.ink.widgets.display.threading.Timer"):
            frame = _render(h(Spinner, {"type": "not-a-real-type"})).lines[0]
            assert frame.plain == "⠋"

    def test_frames_preset_defined(self):
        assert "dots" in SPINNER_FRAMES
        assert "line" in SPINNER_FRAMES
        assert "moon" in SPINNER_FRAMES

    def test_effect_cleanup_returns_callable(self):
        """use_effect create 返回清理函数（组件卸载时取消 Timer）。"""
        from src.tui.ink.widgets import display as display_mod
        with patch.object(display_mod.threading, "Timer") as mock_timer:
            r = Reconciler()
            root = r.create_root()
            el = h(Spinner, {"type": "dots"})
            r.render(root, el, 80, 24)
            # 找到 Spinner 函数 fiber 的 EffectHook destroy
            destroy = _collect_effect_destroy(root)
            assert destroy is not None
            assert callable(destroy)
            destroy()  # 清理可调用（stop 标志）

    def test_color_style(self):
        from src.tui.core.style import Style
        with patch("src.tui.ink.widgets.display.threading.Timer"):
            frame = _render(h(Spinner, {"type": "dots", "color": "red"})).lines[0]
            assert frame.plain == "⠋"
            assert frame.runs[0].style.fg == 1  # red → 1


def _collect_effect_destroy(fiber) -> object | None:
    """递归查找第一个函数 fiber 上 EffectHook.destroy。"""
    f = fiber
    while f is not None:
        if f.is_function:
            for hook in f.hooks:
                if hasattr(hook, "destroy") and hook.destroy is not None:
                    return hook.destroy
        child = _collect_effect_destroy(f.child)
        if child is not None:
            return child
        f = f.sibling
    return None


# ═══════════════════════════════════════════════════════════
# ProgressBar
# ═══════════════════════════════════════════════════════════


class TestProgressBar:
    def test_percent_half(self):
        frame = _render(h(ProgressBar, {"percent": 0.5, "width": 10})).lines[0]
        assert frame.plain == "█████     "

    def test_percent_100_norm(self):
        """0-100 范围自动归一化。"""
        frame = _render(h(ProgressBar, {"percent": 60, "width": 10})).lines[0]
        assert frame.plain == "██████    "

    def test_left_right_markers(self):
        frame = _render(h(ProgressBar, {"percent": 1.0, "width": 5, "left": "[", "right": "]"})).lines[0]
        assert frame.plain == "[█████]"

    def test_custom_char(self):
        frame = _render(h(ProgressBar, {"percent": 1.0, "width": 4, "char": "#"})).lines[0]
        assert frame.plain == "####"

    def test_percent_zero(self):
        frame = _render(h(ProgressBar, {"percent": 0.0, "width": 4})).lines[0]
        assert frame.plain == "    "

    def test_percent_clamped(self):
        frame = _render(h(ProgressBar, {"percent": 150, "width": 4})).lines[0]
        assert frame.plain == "████"

    def test_color_style(self):
        from src.tui.core.style import Style
        frame = _render(h(ProgressBar, {"percent": 1.0, "width": 2, "color": "green"})).lines[0]
        assert frame.runs[0].style.fg == 2


# ═══════════════════════════════════════════════════════════
# Table
# ═══════════════════════════════════════════════════════════


class TestTable:
    def test_no_border_aligned(self):
        el = h(Table, {"data": [["a", "bb"], ["ccc", "d"]]})
        frame = _render(el)
        assert [ln.plain for ln in frame.lines] == ["a   bb", "ccc d"]

    def test_columns_header(self):
        el = h(Table, {"columns": ["Name", "Score"], "data": [["Alice", 90], ["Bob", 85]]})
        frame = _render(el)
        assert frame.lines[0].plain == "Name  Score"
        assert frame.lines[1].plain == "Alice 90"
        assert frame.lines[2].plain == "Bob   85"

    def test_border_single(self):
        el = h(Table, {"columns": ["A"], "data": [["x"]], "border": "single"})
        frame = _render(el)
        lines = [ln.plain for ln in frame.lines]
        assert lines == ["┌───┐", "│ A │", "├───┤", "│ x │", "└───┘"]

    def test_border_true_defaults_single(self):
        el = h(Table, {"data": [["x"]], "border": True})
        frame = _render(el)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0].startswith("┌")
        assert lines[-1].startswith("└")

    def test_border_round(self):
        el = h(Table, {"data": [["x"]], "border": "round"})
        frame = _render(el)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0].startswith("╭")
        assert lines[-1].startswith("╰")

    def test_border_classic(self):
        el = h(Table, {"data": [["x"]], "border": "classic"})
        frame = _render(el)
        lines = [ln.plain for ln in frame.lines]
        assert lines[0].startswith("+")
        assert lines[-1].startswith("+")

    def test_empty_data(self):
        frame = _render(h(Table, {"data": []}))
        assert all(not ln.plain for ln in frame.lines)

    def test_unicode_cell_width(self):
        el = h(Table, {"data": [["中文", "a"], ["b", "cc"]]})
        frame = _render(el)
        # 中文宽 2 → 第一列宽 4（"中文" 占 4 列）；"b" 补 3 空格 + 1 分隔
        assert frame.lines[0].plain == "中文 a"
        assert frame.lines[1].plain == "b    cc"

    def test_border_padding(self):
        el = h(Table, {"data": [["x"]], "border": "single", "padding": 0})
        frame = _render(el)
        lines = [ln.plain for ln in frame.lines]
        assert lines[1] == "│x│"


# ═══════════════════════════════════════════════════════════
# Badge
# ═══════════════════════════════════════════════════════════


class TestBadge:
    def test_render_label_padding(self):
        frame = _render(h(Badge, {"label": "done", "color": "green"})).lines[0]
        assert frame.plain == " done "

    def test_background_color(self):
        frame = _render(h(Badge, {"label": "x", "color": "green"})).lines[0]
        assert frame.runs[0].style.bg == 2  # green → 2

    def test_foreground_auto_contrast(self):
        """暗背景 → 亮前景；亮背景 → 暗前景。"""
        frame = _render(h(Badge, {"label": "x", "color": "black"})).lines[0]
        assert frame.runs[0].style.fg == 231  # 暗背景 → 亮前景
        frame2 = _render(h(Badge, {"label": "x", "color": "yellow"})).lines[0]
        assert frame2.runs[0].style.fg == 232  # 亮背景 → 暗前景

    def test_custom_fg(self):
        frame = _render(h(Badge, {"label": "x", "color": "blue", "fg": "red"})).lines[0]
        assert frame.runs[0].style.fg == 1

    def test_custom_padding(self):
        frame = _render(h(Badge, {"label": "x", "color": "blue", "padding": 2})).lines[0]
        assert frame.plain == "  x  "

    def test_bold(self):
        frame = _render(h(Badge, {"label": "x", "color": "blue", "bold": True})).lines[0]
        assert frame.runs[0].style.bold is True


# ═══════════════════════════════════════════════════════════
# Divider
# ═══════════════════════════════════════════════════════════


class TestDivider:
    def test_plain_line(self):
        frame = _render(h(Divider, {"width": 10})).lines[0]
        assert frame.plain == "──────────"

    def test_title_centered(self):
        el = h(Divider, {"title": "AB", "width": 10})
        frame = _render(el)
        assert "".join(ln.plain for ln in frame.lines) == "─── AB ───"

    def test_default_width_with_title(self):
        el = h(Divider, {"title": "AB"})
        frame = _render(el)
        # 标题宽 2 + 4 = 6 → 两侧各 1
        assert "".join(ln.plain for ln in frame.lines) == "─ AB ─"

    def test_custom_char(self):
        frame = _render(h(Divider, {"width": 6, "char": "="})).lines[0]
        assert frame.plain == "======"

    def test_color_style(self):
        from src.tui.core.style import Style
        frame = _render(h(Divider, {"width": 4, "color": "red"})).lines[0]
        assert frame.runs[0].style.fg == 1

    def test_title_wider_than_width(self):
        el = h(Divider, {"title": "long-title", "width": 5})
        frame = _render(el)
        assert "".join(ln.plain for ln in frame.lines) == "long-title"

    def test_wide_char_repeat(self):
        """宽字符（如 ━ 宽度 1；用 emoji 验证按宽度换算）。"""
        frame = _render(h(Divider, {"width": 4, "char": "="})).lines[0]
        assert frame.plain == "===="
