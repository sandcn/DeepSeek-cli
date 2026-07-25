"""测试消息分隔线全宽渐变（步骤 4 — 消息分隔线全宽渐变）。

覆盖：
  - _make_gradient_sep() 色号序列正确性
  - 渐变分隔线长度自适应（宽屏/窄屏）
  - 窄屏降级缩短行为
  - _make_think_sep() 和 _make_think_end() 渐变正确性
  - 自定义起始/结束色号
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.pipeline.message_display import (
    _make_gradient_sep,
    _make_think_sep,
    _make_think_end,
)


class TestMakeGradientSep:
    """_make_gradient_sep 全宽渐变分隔线测试。"""

    def test_contains_ansi_color_codes(self):
        """返回值包含 ANSI 256 色码和 ━ 字符。"""
        sep = _make_gradient_sep(steps=6)
        assert "38;5;" in sep     # 256 色前景码
        assert "\u2501" in sep    # ━ 厚分隔线
        assert "\033[0m" in sep   # 重置序列

    def test_starts_with_cyan_ends_with_darkgray(self):
        """渐变从青色(45)开始到深灰(237)结束。"""
        sep = _make_gradient_sep(steps=6)
        assert "38;5;45" in sep    # 青色起始
        assert "38;5;237" in sep   # 深灰结束

    def test_correct_number_of_chars(self):
        """指定 steps 时生成对应数量的 ━ 字符。"""
        for n in (4, 8, 12, 20):
            sep = _make_gradient_sep(steps=n)
            count = sep.count("\u2501")
            assert count == n, f"expected {n} ━ chars, got {count}"

    def test_custom_start_end_colors(self):
        """自定义起始结束色的渐变分隔线正确生成。"""
        sep = _make_gradient_sep(start_color=29, end_color=114, steps=6)
        assert "38;5;29" in sep    # 薄荷起始
        assert "38;5;114" in sep   # 薄荷结束

    def test_single_step(self):
        """steps=1 时只生成一个字符。"""
        sep = _make_gradient_sep(start_color=45, end_color=237, steps=1)
        assert sep.count("\u2501") == 1
        assert "38;5;45" in sep    # 仅起始色

    def test_two_steps(self):
        """steps=2 时生成起始和结束色两个字符。"""
        sep = _make_gradient_sep(start_color=45, end_color=237, steps=2)
        assert sep.count("\u2501") == 2
        assert "38;5;45" in sep    # 起始色
        assert "38;5;237" in sep   # 结束色

    def test_has_indent_prefix(self):
        """返回值以两个空格为前缀缩进。"""
        sep = _make_gradient_sep(steps=4)
        assert sep.startswith("  "), f"expected indent prefix, got {repr(sep[:6])}"

    def test_wide_screen_length(self):
        """宽屏（>=80列）时分隔线长度 >= 40。"""
        with patch("src.tui.pipeline.message_display.get_terminal_width", return_value=100):
            sep = _make_gradient_sep()  # steps=0, auto
            count = sep.count("\u2501")
            assert count >= 40, f"wide screen sep too short: {count}"

    def test_narrow_screen_shorter(self):
        """窄屏（<80列）时分隔线长度比宽屏短。"""
        # 宽屏使用 steps=40 的固定长度
        wide_sep = _make_gradient_sep(steps=40)
        # 窄屏模拟：patch 终端宽度 + narrow_sep_width
        with patch("src.tui.pipeline.message_display.get_terminal_width", return_value=70):
            with patch("src.tui.pipeline.message_display.narrow_sep_width", return_value=20):
                narrow_sep = _make_gradient_sep()  # steps=0, auto
                assert narrow_sep.count("\u2501") <= wide_sep.count("\u2501")

    def test_extra_narrow_screen(self):
        """极窄屏（<50列）时不报错，仍生成合理长度分隔线。"""
        with patch("src.tui.pipeline.message_display.narrow_sep_width", return_value=10):
            sep = _make_gradient_sep()
            count = sep.count("\u2501")
            assert count >= 4, f"extra narrow sep too short: {count}"
            assert "38;5;45" in sep
            assert "38;5;237" in sep

    def test_gradient_colors_ordered(self):
        """渐变颜色序列按 start→end 顺序排列。"""
        sep = _make_gradient_sep(start_color=45, end_color=237, steps=10)
        # 提取所有色号，确认第一个色号为45，最后一个为237
        import re
        colors = re.findall(r"38;5;(\d+)", sep)
        assert len(colors) >= 2
        assert int(colors[0]) == 45, f"first color {colors[0]} != 45"
        assert int(colors[-1]) == 237, f"last color {colors[-1]} != 237"

    def test_reverse_gradient(self):
        """反向渐变（深灰→青）也正确生成。"""
        sep = _make_gradient_sep(start_color=237, end_color=45, steps=6)
        import re
        colors = re.findall(r"38;5;(\d+)", sep)
        assert int(colors[0]) == 237
        assert int(colors[-1]) == 45


class TestMakeThinkSep:
    """_make_think_sep 思考分隔线渐变测试。"""

    def test_contains_lightning_and_text(self):
        """思考分隔线包含 ⚡ 闪电图标和「思考」文本。"""
        sep = _make_think_sep()
        assert "\u26a1" in sep     # ⚡ 闪电图标
        assert "\u601d\u8003" in sep  # 思考

    def test_contains_256_colors(self):
        """思考分隔线包含 256 色 ANSI 码。"""
        sep = _make_think_sep()
        assert "38;5;45" in sep    # 青色
        assert "38;5;237" in sep   # 深灰
        assert "\033[0m" in sep    # 重置

    def test_has_reset(self):
        """思考分隔线以重置序列结尾。"""
        sep = _make_think_sep()
        assert sep.endswith("\033[0m")

    def test_narrow_screen(self):
        """窄屏下思考分隔线仍正常生成。"""
        with patch("src.tui.pipeline.message_display.get_terminal_width", return_value=60):
            sep = _make_think_sep()
            assert "\u26a1" in sep
            assert sep.endswith("\033[0m")


class TestMakeThinkEnd:
    """_make_think_end 思考结束标记渐变测试。"""

    def test_contains_ansi_codes(self):
        """思考结束标记包含 ANSI 256 色码和 ━ 字符。"""
        end = _make_think_end()
        assert "38;5;" in end
        assert "\u2501" in end
        assert "\033[0m" in end

    def test_narrow_screen(self):
        """窄屏下思考结束标记仍正常生成。"""
        with patch("src.tui.pipeline.message_display.get_terminal_width", return_value=60):
            end = _make_think_end()
            assert "38;5;" in end
            assert "\u2501" in end


class TestNarrowScreenDegradation:
    """窄屏降级行为集成测试。"""

    def test_all_separators_work_on_narrow(self):
        """所有分隔线函数在窄屏（60列）下均不报错。"""
        with patch("src.tui.pipeline.message_display.get_terminal_width", return_value=60):
            # _make_gradient_sep 使用 narrow_sep_width 计算宽度
            with patch("src.tui.pipeline.message_display.narrow_sep_width", return_value=20):
                result = _make_gradient_sep()
                assert isinstance(result, str)
                assert len(result) > 0
            for make_fn in (_make_think_sep, _make_think_end):
                result = make_fn()
                assert isinstance(result, str)
                assert len(result) > 0

    def test_all_separators_work_on_extra_narrow(self):
        """所有分隔线函数在极窄屏（35列）下均不报错。"""
        with patch("src.tui.pipeline.message_display.get_terminal_width", return_value=35):
            with patch("src.tui.pipeline.message_display.narrow_sep_width", return_value=10):
                result = _make_gradient_sep()
                assert isinstance(result, str)
                assert len(result) > 0
            for make_fn in (_make_think_sep, _make_think_end):
                result = make_fn()
                assert isinstance(result, str)
                assert len(result) > 0
