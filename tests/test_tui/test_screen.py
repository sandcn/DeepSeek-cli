"""测试 _screen.py — 终端屏幕管理函数。

验证 ANSI 序列生成和字符宽度计算的正确性。
"""
from src.tui._screen import (
    wcswidth_simple,
    set_scroll_region,
    reset_scroll_region,
    cursor_goto,
    cursor_save,
    cursor_restore,
    cursor_up,
    cursor_down,
    clear_line,
    clear_screen_from_cursor,
    move_clear,
    scroll_up,
    scroll_down,
    sgr,
    sgr_reset,
    fg_256,
    bg_256,
    _get_terminal_size,
    TerminalWidthCache,
    narrow_sep_width,
)


class TestWcswidth:
    """测试字符宽度计算。"""

    def test_ascii(self):
        assert wcswidth_simple("hello") == 5
        assert wcswidth_simple("Hello World") == 11
        assert wcswidth_simple("") == 0

    def test_cjk(self):
        assert wcswidth_simple("你好") == 4
        assert wcswidth_simple("世界") == 4
        assert wcswidth_simple("你好世界") == 8

    def test_mixed(self):
        assert wcswidth_simple("hello你好") == 9  # 5 + 4

    def test_control_chars(self):
        # 控制字符宽度为 0
        assert wcswidth_simple("\x1b[31m") == 4  # ESC + [ + 3 + 1 + m

    def test_zero_width(self):
        # 组合标记宽度为 0
        assert wcswidth_simple("\u0300") == 0  # combining grave

    def test_fullwidth(self):
        assert wcswidth_simple("\uff01") == 2  # ！


class TestScrollRegion:
    """测试滚动区域序列。"""

    def test_set_scroll_region(self):
        assert set_scroll_region(1, 20) == "\033[1;20r"

    def test_reset_scroll_region(self):
        assert reset_scroll_region() == "\033[r"


class TestCursorControl:
    """测试光标控制序列。"""

    def test_cursor_save(self):
        assert cursor_save() == "\033[s"

    def test_cursor_restore(self):
        assert cursor_restore() == "\033[u"

    def test_cursor_goto(self):
        assert cursor_goto(10, 5) == "\033[10;5H"

    def test_cursor_up(self):
        assert cursor_up(3) == "\033[3A"
        assert cursor_up() == "\033[1A"

    def test_cursor_down(self):
        assert cursor_down(2) == "\033[2B"


class TestClear:
    """测试清屏/清行序列。"""

    def test_clear_line(self):
        assert clear_line() == "\r\033[K"

    def test_clear_screen_from_cursor(self):
        assert clear_screen_from_cursor() == "\033[0J"

    def test_move_clear(self):
        assert move_clear(5) == "\033[5;1H\033[K"


class TestScroll:
    """测试滚动序列。"""

    def test_scroll_up(self):
        assert scroll_up(3) == "\033[3S"

    def test_scroll_down(self):
        assert scroll_down(2) == "\033[2T"


class TestSGR:
    """测试 SGR 序列。"""

    def test_sgr_reset(self):
        assert sgr_reset() == "\033[0m"

    def test_sgr_single(self):
        assert sgr(1) == "\033[1m"

    def test_sgr_multiple(self):
        assert sgr(1, 31) == "\033[1;31m"

    def test_sgr_empty(self):
        assert sgr() == "\033[0m"

    def test_fg_256(self):
        assert fg_256(196) == "\033[38;5;196m"

    def test_bg_256(self):
        assert bg_256(47) == "\033[48;5;47m"


class TestTerminalSize:
    """测试终端尺寸获取。"""

    def test_get_terminal_size_returns_tuple(self):
        result = _get_terminal_size()
        assert isinstance(result, tuple)
        assert len(result) == 2
        cols, rows = result
        assert isinstance(cols, int)
        assert isinstance(rows, int)
        assert cols > 0
        assert rows > 0


class TestTerminalWidthCache:
    """测试终端宽度缓存。"""

    def test_singleton(self):
        c1 = TerminalWidthCache.get_default()
        c2 = TerminalWidthCache.get_default()
        assert c1 is c2

    def test_get_width(self):
        w = TerminalWidthCache.get_default().get_width()
        assert isinstance(w, int)
        assert w > 0


class TestNarrowSepWidth:
    """测试窄屏分隔线宽度。"""

    def test_normal_width(self):
        assert narrow_sep_width(120, threshold=40) == 120

    def test_narrow_width(self):
        assert narrow_sep_width(30, threshold=40) == 28  # max(10, 30-2)

    def test_very_narrow(self):
        assert narrow_sep_width(5, threshold=40) == 10  # max(10, 5-2)
