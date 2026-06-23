"""_estimate_content_lines() 终端宽度感知测试

测试范围：
1. 空文本 → 返回 1
2. 单行短文本（不换行） → 返回 1
3. 多行文本 → 按换行计数（短行不额外换行）
4. 长行换行 → 按终端宽度拆分
5. CJK 宽字符 → 'W'/'F' 类字符占 2 列
6. 终端宽度获取失败 → 回退纯换行计数
7. 终端宽度为 0 → 回退纯换行计数
8. TTL 缓存 → 2 秒内不重复调用 get_terminal_size()
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.chat_ui import _components


# ── Helpers ────────────────────────────────────────────

def reset_cache() -> None:
    """重置模块级 _term_width_cache 到初始状态。"""
    _components._term_width_cache = (0.0, 80)


def _make_terminal_size(columns: int):
    """构造一个 namedtuple-like 对象模拟 os.terminal_size。"""
    from collections import namedtuple
    TS = namedtuple("terminal_size", ["columns", "lines"])
    return TS(columns=columns, lines=24)


# ── 基础测试 ──────────────────────────────────────────

class TestEstimateContentLinesBasic:
    """基础场景：不依赖终端宽度的纯换行计数。"""

    def test_empty_text_returns_1(self):
        """空文本 → 返回 1（至少占一行）。"""
        assert _components._estimate_content_lines("") == 1

    def test_single_line_short(self):
        """单行短文本 → 返回 1。"""
        assert _components._estimate_content_lines("hello") == 1

    def test_multi_newlines(self):
        """多行纯文本 → 按换行数 + 1 计算。"""
        assert _components._estimate_content_lines("a\nb\nc") == 3

    def test_trailing_newline(self):
        """末尾有换行符 → 行数 + 1。"""
        assert _components._estimate_content_lines("line\n") == 2

    def test_multiple_trailing_newlines(self):
        """多个末尾换行符 → 每个 \\n 产生一个空行。"""
        assert _components._estimate_content_lines("a\n\n") == 3


# ── 终端宽度换行测试 ──────────────────────────────────

class TestEstimateContentLinesWrapping:
    """终端宽度感知换行：mock get_terminal_size 验证长行拆分。"""

    def test_long_line_wraps_at_width(self):
        """终端宽度 10，25 个 ASCII 字符 → ceil(25/10)=3 行。"""
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("a" * 25)
        assert result == 3

    def test_exact_width_no_wrap(self):
        """终端宽度 10，10 个 ASCII 字符 → 刚好 1 行。"""
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("a" * 10)
        assert result == 1

    def test_width_plus_one_wraps(self):
        """终端宽度 10，11 个 ASCII 字符 → 2 行。"""
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("a" * 11)
        assert result == 2

    def test_multi_line_with_wrapping(self):
        """多行 + 长行：终端宽度 5，"aaaaa\nbb" → 第 1 行 1 行 + 第 2 行 1 行 = 2。"""
        reset_cache()
        ts = _make_terminal_size(5)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("aaaaa\nbb")
        assert result == 2

    def test_multi_line_long_wrapping(self):
        """多行 + 超长行：终端宽度 5，"aaaaa\nbbbbbb" → 1 + ceil(6/5)=2 → 3 行。"""
        reset_cache()
        ts = _make_terminal_size(5)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("aaaaa\nbbbbbb")
        assert result == 3


# ── CJK 宽字符测试 ────────────────────────────────────

class TestEstimateContentLinesCJK:
    """CJK 宽字符：'W'/'F' 类字符占 2 列。"""

    def test_cjk_fullwidth_chars(self):
        """终端宽度 10，5 个中文全角字符（各 2 列）→ 10 列 = 1 行。"""
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("你好世界！")
        assert result == 1

    def test_cjk_wrap_at_width(self):
        """终端宽度 4，3 个中文字符（各 2 列，共 6 列）→ ceil(6/4)=2 行。"""
        reset_cache()
        ts = _make_terminal_size(4)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("你好吗")
        assert result == 2

    def test_mixed_ascii_cjk(self):
        """终端宽度 10，"abc你好" → 3*1 + 2*2 = 7 列 → 1 行。"""
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("abc你好")
        assert result == 1

    def test_mixed_ascii_cjk_wrap(self):
        """终端宽度 10，"abcdef你好吗" → 6*1 + 3*2 = 12 列 → ceil(12/10)=2 行。"""
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("abcdef你好吗")
        assert result == 2

    def test_narrow_cjk_ambiguous(self):
        """'A'（Ambiguous）类字符在 CJK 语境占 2 列，但 east_asian_width 返回 'A'，
        不在 'WF' 中 → 按 1 列处理。"""
        # '·' (U+00B7, MIDDLE DOT) 的 east_asian_width 通常是 'A'（Ambiguous）
        # 不匹配 'WF'，按 1 列处理。
        import unicodedata
        try:
            w = unicodedata.east_asian_width("·")
        except Exception:
            w = "Na"
        if w in "WF":
            pytest.skip("当前环境下 '·' 被归类为宽字符")
        reset_cache()
        ts = _make_terminal_size(10)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("·" * 15)
        # 15 个 · 各占 1 列 → 15 列，终端宽 10 → ceil(15/10)=2
        assert result == 2


# ── 回退测试 ──────────────────────────────────────────

class TestEstimateContentLinesFallback:
    """终端宽度不可用时的回退行为。"""

    def test_get_terminal_size_raises_fallback(self):
        """shutil.get_terminal_size() 抛异常 → 回退到 count('\\n') + 1。"""
        reset_cache()
        with patch("shutil.get_terminal_size", side_effect=OSError("not a tty")):
            result = _components._estimate_content_lines("hello\nworld")
        assert result == 2  # 纯换行计数

    def test_width_zero_fallback(self):
        """终端宽度返回 0 → 回退到 count('\\n') + 1。"""
        reset_cache()
        ts = _make_terminal_size(0)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("hello\nworld")
        assert result == 2

    def test_width_negative_fallback(self):
        """终端宽度返回负数 → 回退到 count('\\n') + 1。"""
        reset_cache()
        ts = _make_terminal_size(-5)
        with patch("shutil.get_terminal_size", return_value=ts):
            result = _components._estimate_content_lines("hello\nworld")
        assert result == 2


# ── 缓存测试 ──────────────────────────────────────────

class TestTerminalWidthCache:
    """_term_width_cache TTL 2s 缓存行为。"""

    def test_cache_hit_within_ttl(self):
        """2 秒内连续调用 → 仅调用一次 get_terminal_size()。"""
        reset_cache()
        ts = _make_terminal_size(40)
        with patch("shutil.get_terminal_size", return_value=ts) as mock_gs:
            r1 = _components._get_terminal_width()
            r2 = _components._get_terminal_width()
        assert r1 == 40
        assert r2 == 40
        mock_gs.assert_called_once()  # 缓存命中，仅调用一次

    def test_cache_expired_refresh(self):
        """超过 2 秒后 → 重新调用 get_terminal_size()。"""
        reset_cache()
        ts1 = _make_terminal_size(40)
        ts2 = _make_terminal_size(100)
        # 第一次调用：时间戳 10.0，缓存已过期
        with patch.object(_components.time, "monotonic", return_value=10.0):
            with patch.object(_components.shutil, "get_terminal_size", return_value=ts1):
                r1 = _components._get_terminal_width()
        assert r1 == 40

        # 第二次调用：时间戳 13.0（推进 3 秒），缓存再次过期
        with patch.object(_components.time, "monotonic", return_value=13.0):
            with patch.object(_components.shutil, "get_terminal_size", return_value=ts2):
                r2 = _components._get_terminal_width()
        assert r2 == 100

    def test_cache_default_value(self):
        """模块初始状态：_term_width_cache = (0.0, 80)。

        缓存未命中时调用真实 get_terminal_size()，
        返回值取决于实际终端环境（可能不为 80）。
        此处仅验证不崩溃且返回正整数。
        """
        reset_cache()
        with patch.object(_components.time, "monotonic", return_value=5.0):
            w = _components._get_terminal_width()
        assert isinstance(w, int)
        assert w > 0

    def test_cache_on_exception_returns_default(self):
        """get_terminal_size() 异常时缓存默认值 80。"""
        reset_cache()
        # 推进时间使缓存失效
        _components._term_width_cache = (0.0, 80)  # 确保初始状态
        with patch("time.monotonic", return_value=5.0):
            with patch("shutil.get_terminal_size", side_effect=OSError):
                w = _components._get_terminal_width()
        assert w == 80
        # 缓存已更新
        assert _components._term_width_cache[1] == 80
        assert _components._term_width_cache[0] == 5.0


# ── 真实终端宽度集成测试 ──────────────────────────────

class TestEstimateContentLinesRealTerminal:
    """使用真实终端宽度（不 mock）的集成测试。"""

    def test_real_terminal_no_crash(self):
        """真实终端环境下不崩溃。"""
        reset_cache()
        # 使缓存失效
        _components._term_width_cache = (0.0, 80)
        with patch("time.monotonic", return_value=100.0):
            result = _components._estimate_content_lines("test")
        assert result >= 1

    def test_long_text_real_terminal(self):
        """长文本在真实终端下估算行数 ≥ 换行数 + 1。"""
        reset_cache()
        _components._term_width_cache = (0.0, 80)
        with patch("time.monotonic", return_value=100.0):
            text = "x" * 200
            result = _components._estimate_content_lines(text)
        # 不崩溃且结果合理
        assert result >= 1
