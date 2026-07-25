"""窄屏自适应模块测试"""

from src.tui.terminal.narrow import (
    is_narrow,
    narrow_truncate,
    narrow_indent,
    narrow_sep_width,
    get_terminal_width,
)


class TestNarrowDetection:
    """窄屏检测函数测试"""

    def test_get_terminal_width_returns_int(self):
        w = get_terminal_width()
        assert isinstance(w, int)
        assert w > 0

    def test_is_narrow_is_bool(self):
        r = is_narrow()
        assert isinstance(r, bool)


class TestNarrowTruncate:
    """narrow_truncate 自适应截断函数测试"""

    def test_truncate_normal_width(self):
        """正常宽度下返回 normal 值"""
        # get_terminal_width() >= 80 时返回 normal
        w = get_terminal_width()
        if w >= 80:
            assert narrow_truncate(100, 50, 20) == 100
            assert narrow_truncate(60) == 60

    def test_truncate_with_custom_values(self):
        """传入自定义窄屏/极窄屏值"""
        result = narrow_truncate(100, 50, 20)
        w = get_terminal_width()
        if w >= 80:
            assert result == 100
        elif w >= 50:
            assert result == 50
        else:
            assert result == 20


class TestNarrowIndent:
    """narrow_indent 自适应缩进测试"""

    def test_indent_normal_width(self):
        """正常宽度下返回传入的正常值"""
        w = get_terminal_width()
        if w >= 80:
            assert narrow_indent(2) == 2
            assert narrow_indent(4) == 4

    def test_indent_returns_at_least_zero(self):
        """确保返回值非负"""
        assert narrow_indent(0) >= 0
        assert narrow_indent(2) >= 0


class TestNarrowSepWidth:
    """narrow_sep_width 自适应分隔线宽度测试"""

    def test_sep_width_returns_positive_int(self):
        """返回正整数"""
        w = narrow_sep_width(40)
        assert isinstance(w, int)
        assert w >= 10

    def test_sep_width_smaller_than_tw(self):
        """分隔线不超过终端宽度"""
        tw = get_terminal_width()
        w = narrow_sep_width(40)
        assert w <= tw
