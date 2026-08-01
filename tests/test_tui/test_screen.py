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

    def test_regional_indicator_pair_width_regression(self):
        """RI 码点（0x1F1E6-0x1F1FF）宽度修复：成对 RI 按 2 列、单 RI 按 1 列。

        修复前 (0x1F000, 0x1FAFF) 覆盖 RI → 🇨🇳 误计 4；修复后排除 RI，
        单 RI 宽 1（保守语义）、成对 RI（国旗）1×2=2，与主流 wcwidth 一致。
        """
        # 🇨 = U+1F1E8（RI），🇳 = U+1F1F3（RI）→ 1+1=2（修复前 4）
        assert wcswidth_simple("\U0001F1E8\U0001F1F3") == 2
        # 单 RI 宽 1（保守：部分终端按 2 列渲染，本实现取 wcwidth 语义）
        assert wcswidth_simple("\U0001F1E8") == 1
        # 常规 emoji（📖 U+1F4D6）仍宽 2，行为不变
        assert wcswidth_simple("\U0001F4D6") == 2

    def test_zwj_sequence_width_regression(self):
        """ZWNJ/ZWJ（0x200C/0x200D）宽度修复：零宽，emoji ZWJ 序列按组件累加。

        👨👩👧 = U+1F468 + ZWJ + U+1F469 + ZWJ + U+1F467 → 2+0+2+0+2 = 6
        （修复前 ZWJ 误计 1 → 8，组件间溢出）。
        """
        assert wcswidth_simple("\u200D") == 0  # ZWJ 本身零宽
        assert wcswidth_simple("\u200C") == 0  # ZWNJ 本身零宽
        assert wcswidth_simple(
            "\U0001F468\u200D\U0001F469\u200D\U0001F467"
        ) == 6  # 各组件宽度之和（不溢出）


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


class TestNarrowSepWidth:
    """测试窄屏分隔线宽度。"""

    def test_normal_width(self):
        assert narrow_sep_width(120, threshold=40) == 120

    def test_narrow_width(self):
        assert narrow_sep_width(30, threshold=40) == 28  # max(10, 30-2)

    def test_very_narrow(self):
        assert narrow_sep_width(5, threshold=40) == 10  # max(10, 5-2)


class TestColorConstants:
    """方向F 步骤12 — ANSI 颜色常量唯一真源收敛回归测试。"""

    def test_color_constants_reexport_regression(self):
        """_const 与 _screen 的 _COLOR_* 值相等（_screen re-export 保持路径）。"""
        from src.tui._const import (
            _COLOR_ACCENT, _COLOR_DEEP_CYAN, _COLOR_DIM, _COLOR_RESET,
            _COLOR_SEP, _COLOR_TIME, _COLOR_TOKEN, _COLOR_SPEED,
            _COLOR_TOOL_OK, _COLOR_TOOL_FAIL, _COLOR_SELECT_BG,
            _COLOR_SELECT_FG, _COLOR_COMPLETE_TITLE,
            _COLOR_COMPLETE_CMD_PREFIX, _COLOR_COMPLETE_DIR,
            _COLOR_COMPLETE_MATCH,
        )
        from src.tui._screen import (
            _COLOR_ACCENT as S_ACCENT,
            _COLOR_DEEP_CYAN as S_DEEP_CYAN,
            _COLOR_DIM as S_DIM,
            _COLOR_RESET as S_RESET,
            _COLOR_SEP as S_SEP,
            _COLOR_TIME as S_TIME,
            _COLOR_TOKEN as S_TOKEN,
            _COLOR_SPEED as S_SPEED,
            _COLOR_TOOL_OK as S_TOOL_OK,
            _COLOR_TOOL_FAIL as S_TOOL_FAIL,
            _COLOR_SELECT_BG as S_SELECT_BG,
            _COLOR_SELECT_FG as S_SELECT_FG,
            _COLOR_COMPLETE_TITLE as S_TITLE,
            _COLOR_COMPLETE_CMD_PREFIX as S_CMD_PREFIX,
            _COLOR_COMPLETE_DIR as S_DIR,
            _COLOR_COMPLETE_MATCH as S_MATCH,
        )
        assert _COLOR_ACCENT == S_ACCENT
        assert _COLOR_DEEP_CYAN == S_DEEP_CYAN
        assert _COLOR_DIM == S_DIM
        assert _COLOR_RESET == S_RESET
        assert _COLOR_SEP == S_SEP
        assert _COLOR_TIME == S_TIME
        assert _COLOR_TOKEN == S_TOKEN
        assert _COLOR_SPEED == S_SPEED
        assert _COLOR_TOOL_OK == S_TOOL_OK
        assert _COLOR_TOOL_FAIL == S_TOOL_FAIL
        assert _COLOR_SELECT_BG == S_SELECT_BG
        assert _COLOR_SELECT_FG == S_SELECT_FG
        assert _COLOR_COMPLETE_TITLE == S_TITLE
        assert _COLOR_COMPLETE_CMD_PREFIX == S_CMD_PREFIX
        assert _COLOR_COMPLETE_DIR == S_DIR
        assert _COLOR_COMPLETE_MATCH == S_MATCH

        # P2-15：关键色硬编码值锚点（防常量值漂移，先 read_file _const.py 确认值）
        assert _COLOR_ACCENT == "\033[38;5;45m"
        assert _COLOR_RESET == "\033[0m"
        assert _COLOR_SPEED == "\033[38;5;214m"
        assert _COLOR_TOOL_OK == "\033[38;5;41m"

    def test_emergency_constants_in_const_regression(self):
        """ANSI_EMERGENCY_* 在 _const 可导入（引擎紧急路径依赖，值不变）。"""
        from src.tui._const import (
            ANSI_EMERGENCY_RED, ANSI_EMERGENCY_YELLOW,
            ANSI_EMERGENCY_RESET, ANSI_EMERGENCY_CURSOR_BOTTOM,
        )
        assert ANSI_EMERGENCY_RED == "\033[31m"
        assert ANSI_EMERGENCY_YELLOW == "\033[33m"
        assert ANSI_EMERGENCY_RESET == "\033[0m"
        assert ANSI_EMERGENCY_CURSOR_BOTTOM == "\033[9999;1H"


class TestSigwinchSignalSafe:
    """BUG-T4 — SIGWINCH 信号安全：处理器只置标志，渲染循环轮询。"""

    def _install_callback(self, cb):
        """注册回调并返回清理函数。"""
        import src.tui._screen as scr
        scr._sigwinch_callbacks.append(cb)

        def cleanup():
            scr._sigwinch_callbacks.remove(cb)
            scr._sigwinch_pending = False

        return cleanup

    def test_sigwinch_handler_only_sets_flag_regression(self):
        """调用 _handle_sigwinch 不立即调用回调（仅置 pending 标志）。"""
        from unittest.mock import MagicMock
        import src.tui._screen as scr

        cb = MagicMock()
        cleanup = self._install_callback(cb)
        try:
            scr._sigwinch_pending = False
            scr._handle_sigwinch(28, None)  # SIGWINCH 在 Linux 为 28
            cb.assert_not_called()
            assert scr._sigwinch_pending is True
        finally:
            cleanup()

    def test_process_sigwinch_invokes_callbacks_regression(self):
        """process_sigwinch 复位 pending 并在线程上下文调用回调。"""
        from unittest.mock import MagicMock, patch
        import src.tui._screen as scr

        cb = MagicMock()
        cleanup = self._install_callback(cb)
        try:
            scr._sigwinch_pending = True
            with patch.object(scr, "_get_terminal_size", return_value=(120, 40)):
                result = scr.process_sigwinch()
            assert result is True
            cb.assert_called_once_with(120, 40)
            assert scr._sigwinch_pending is False
        finally:
            cleanup()

    def test_process_sigwinch_no_pending_regression(self):
        """无 pending 时 process_sigwinch 返回 False（不调用回调）。"""
        from unittest.mock import MagicMock
        import src.tui._screen as scr

        cb = MagicMock()
        cleanup = self._install_callback(cb)
        try:
            scr._sigwinch_pending = False
            assert scr.process_sigwinch() is False
            cb.assert_not_called()
        finally:
            cleanup()

    def test_callback_exception_isolated_regression(self):
        """单个回调异常不中断其他回调。"""
        from unittest.mock import MagicMock, patch
        import src.tui._screen as scr

        cb_ok = MagicMock()
        cb_bad = MagicMock(side_effect=RuntimeError("boom"))
        cleanup1 = self._install_callback(cb_ok)
        cleanup2 = self._install_callback(cb_bad)
        try:
            scr._sigwinch_pending = True
            with patch.object(scr, "_get_terminal_size", return_value=(80, 24)):
                result = scr.process_sigwinch()
            assert result is True
            cb_ok.assert_called_once_with(80, 24)
        finally:
            cleanup1()
            cleanup2()


class TestDetectTruecolor:
    """方向B 步骤12 — 终端能力协商（COLORTERM 环境变量检测）。"""

    def test_detect_truecolor_true(self):
        from unittest.mock import patch
        from src.tui._screen import detect_truecolor
        with patch.dict("os.environ", {"COLORTERM": "truecolor"}):
            assert detect_truecolor() is True

    def test_detect_truecolor_24bit(self):
        from unittest.mock import patch
        from src.tui._screen import detect_truecolor
        with patch.dict("os.environ", {"COLORTERM": "24bit"}):
            assert detect_truecolor() is True

    def test_detect_truecolor_case_insensitive(self):
        from unittest.mock import patch
        from src.tui._screen import detect_truecolor
        with patch.dict("os.environ", {"COLORTERM": "TrueColor"}):
            assert detect_truecolor() is True

    def test_detect_truecolor_default_false(self):
        """未设置 COLORTERM → 默认 False（安全兜底，走 256 降级）。"""
        from unittest.mock import patch
        from src.tui._screen import detect_truecolor
        with patch.dict("os.environ", {}, clear=True):
            assert detect_truecolor() is False

    def test_detect_truecolor_unknown_value_false(self):
        """COLORTERM 为 256color 等未知值 → False（不误判）。"""
        from unittest.mock import patch
        from src.tui._screen import detect_truecolor
        with patch.dict("os.environ", {"COLORTERM": "256color"}):
            assert detect_truecolor() is False


class TestTerminalWidthCacheDimensions:
    """方向1 — get_dimensions 高度走独立 TTL（height 过期时刷新，宽度缓存命中）。"""

    def test_get_dimensions_refreshes_height_ttl_regression(self):
        """get_dimensions 高度经 get_height() 独立 TTL 检查。

        场景：height TTL 已过期（width TTL 未过期），终端尺寸已变 (100,30)
        ——修复前直接读 ``_height`` 字段（40）绕过 height TTL → 返回陈旧高度
        40；修复后经 get_height() 刷新为 30（宽度缓存命中保持 120）。
        """
        import time as _time
        from unittest.mock import patch
        from src.tui._screen import TerminalWidthCache
        cache = TerminalWidthCache(ttl=60.0)
        cache._width, cache._height = 120, 40
        cache._last_width_fetch = _time.monotonic()            # width TTL 未过期
        cache._last_height_fetch = _time.monotonic() - 100.0   # height TTL 已过期
        with patch("src.tui._screen._get_terminal_size", return_value=(100, 30)):
            assert cache.get_width() == 120  # width 缓存命中（未过期）
            w, h = cache.get_dimensions()
        assert (w, h) == (120, 30), f"高度应刷新为 30，实际 {h}（陈旧高度 40）"

    def test_get_dimensions_width_cache_hit_keeps_width_regression(self):
        """width TTL 未过期时 get_dimensions 宽度保持缓存值（不强制刷新）。"""
        import time as _time
        from unittest.mock import patch
        from src.tui._screen import TerminalWidthCache
        cache = TerminalWidthCache(ttl=60.0)
        cache._width, cache._height = 120, 40
        cache._last_width_fetch = _time.monotonic()
        cache._last_height_fetch = _time.monotonic() - 100.0
        with patch("src.tui._screen._get_terminal_size", return_value=(100, 30)):
            w, h = cache.get_dimensions()
        # 宽度缓存命中（120 保持），高度刷新（30）
        assert (w, h) == (120, 30)

    def test_width_cache_timestamps_regression(self):
        """get_width/get_height 过期刷新后两个时间戳同步更新（回归确认）。

        bug 清单快照行号过时——当前实现 get_width 刷新时已同步更新
        ``_last_height_fetch``（get_height 同理），本测试锁定不回归：
        get_width 过期刷新 → 两个 fetch 时间戳均推进；get_height 同理。
        """
        import time as _time
        from unittest.mock import patch
        from src.tui._screen import TerminalWidthCache
        cache = TerminalWidthCache(ttl=60.0)
        cache._width, cache._height = 120, 40
        old = _time.monotonic() - 100.0  # 两个 TTL 均过期
        cache._last_width_fetch = old
        cache._last_height_fetch = old
        with patch("src.tui._screen._get_terminal_size", return_value=(100, 30)):
            assert cache.get_width() == 100
            assert cache._last_width_fetch > old, "get_width 过期刷新后 _last_width_fetch 应推进"
            assert cache._last_height_fetch > old, (
                "get_width 过期刷新后 _last_height_fetch 应同步推进（回归确认）"
            )
            # get_height 同理：两个时间戳均过期 → 刷新后同步推进
            cache._last_width_fetch = old
            cache._last_height_fetch = old
            assert cache.get_height() == 30
            assert cache._last_height_fetch > old, "get_height 过期刷新后 _last_height_fetch 应推进"
            assert cache._last_width_fetch > old, (
                "get_height 过期刷新后 _last_width_fetch 应同步推进（回归确认）"
            )
