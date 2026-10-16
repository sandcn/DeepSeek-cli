"""流式超长单行行内解析性能优化（第三批）测试。

本轮改动（`tui 流式 markdown 优化超长单行文本渲染性能`）——消除行内解析在
「重复定界符 run / 缺闭合标记」的超长单行上的 O(n²) 退化：

1. ``renderer._inline_scan``（新模块）：``find_first_of``（C 级定位首个目标
   字符）、``char_presence_index``（长文本字符存在索引）、``DelimiterRunCache``
   （连续同字符 run 终点缓存）；
2. ``_InlineParser``：字符存在索引 / 终止序列探测的**必要条件快速判否**、
   定界符 run 终点与 flanking 判定记忆化；
3. 逐字符扫描 → C 级 ``str.find``：``_try_wikilink`` / ``_try_footnote_ref`` /
   ``_try_angle_autolink`` / ``_try_html_entity`` / ``_try_html_tag``（标签名
   长度上限）/ ``_try_paren_math`` / ``_parse_html_content(_nested)``；
4. ``_parse_italic_content`` 普通文本段跳段（与 ``_parse_until`` 同口径）；
5. ``render_inline_to_text`` 的 ``TextNode`` 类型快路径。

正确性（语义不变）由「原样输出 / 正常渲染」用例锁定，性能由「线性度 + 宽松
上界」用例防回归。
"""

from __future__ import annotations

import random
import time

import pytest

from src.renderer.ansi.inline import render_inline
from src.renderer.inline_parser import _InlineParser
from src.renderer._inline_scan import (
    CHAR_INDEX_MIN,
    DelimiterRunCache,
    char_presence_index,
    find_first_of,
)


def _plain(text: str) -> str:
    return "".join(r.text for r in render_inline(text))


def _runs(text: str):
    return [(r.text, r.style.fg, bool(r.style.bold) if r.style else False,
             bool(r.style.italic) if r.style else False)
            for r in render_inline(text)]


def _elapsed(fn, repeats: int = 3) -> float:
    fn()
    t0 = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - t0) / repeats


# ═══════════════════════════════════════════════════════════
# 1. 扫描原语
# ═══════════════════════════════════════════════════════════


def _find_first_of_reference(text: str, chars: str, start: int = 0) -> int:
    """逐字符参考实现。"""
    for i in range(start, len(text)):
        if text[i] in chars:
            return i
    return -1


class TestFindFirstOf:
    CASES = [
        ("", "; ", 0),
        ("abc", ";", 0),
        ("a;b;c", ";", 0),
        ("a;b;c", ";", 3),
        ("a b\tc\nd", "; \t\n", 0),
        ("中文字", "字", 0),
        ("xxxx", "xxxx", 2),
    ]

    def test_matches_reference(self):
        for text, chars, start in self.CASES:
            assert find_first_of(text, chars, start) == \
                _find_first_of_reference(text, chars, start)

    def test_empty_chars(self):
        assert find_first_of("abc", "") == -1

    def test_single_char_uses_find(self):
        assert find_first_of("abcdef", "d") == 3
        assert find_first_of("abcdef", "d", 4) == -1

    def test_fuzz_matches_reference(self):
        rnd = random.Random(20261012)
        alphabet = "ab; \t\n[]&<>中"
        for _ in range(300):
            text = "".join(rnd.choice(alphabet)
                           for _ in range(rnd.randint(0, 40)))
            chars = "".join(rnd.sample("; \t\n[]&<>", rnd.randint(1, 4)))
            start = rnd.randint(0, len(text))
            assert find_first_of(text, chars, start) == \
                _find_first_of_reference(text, chars, start), (text, chars)


class TestCharPresenceIndex:
    def test_short_text_returns_none(self):
        assert char_presence_index("a" * (CHAR_INDEX_MIN - 1)) is None

    def test_long_text_indexes_chars(self):
        idx = char_presence_index("a" * CHAR_INDEX_MIN + "]")
        assert idx is not None and "]" in idx and "x" not in idx


class TestDelimiterRunCache:
    def test_matches_direct_scan(self):
        rnd = random.Random(20261013)
        alphabet = "a*_ |+~"
        for _ in range(300):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))
            cache = DelimiterRunCache()
            for pos in range(len(text)):
                for ch in "*_+~":
                    end = cache.run_end(text, pos, ch)
                    ref = pos
                    while ref < len(text) and text[ref] == ch:
                        ref += 1
                    assert end == ref, (text, pos, ch)

    def test_hit_across_positions(self):
        cache = DelimiterRunCache()
        text = "*" * 1000
        assert cache.run_end(text, 0, "*") == 1000
        # 同一 run 内的后续查询直接命中缓存（值一致）
        assert cache.run_end(text, 500, "*") == 1000

    def test_non_char_position_yields_zero_run(self):
        cache = DelimiterRunCache()
        assert cache.run_end("abc", 1, "*") == 1

    def test_reset(self):
        cache = DelimiterRunCache()
        cache.run_end("*" * 10, 0, "*")
        cache.reset()
        assert cache.run_end("ab", 0, "a") == 1


# ═══════════════════════════════════════════════════════════
# 2. 必要条件判否（无闭合标记 → 原样输出）
# ═══════════════════════════════════════════════════════════


class TestNoCloseMarkupKeptLiteral:
    @pytest.mark.parametrize("text", [
        "[" * 200,                      # 无 ] → 链接/图片/脚注/WikiLink 均不成立
        "[" * 400,
        "&" * 200,                      # 无 ; → 实体不成立
        "&" * 400,
        "(" * 200,
        "<a" * 100,                      # 无 > → HTML 标签/自动链接不成立
        "<" * 200,
        "[x" * 100,                      # 未闭合链接文字整体字面保留
    ])
    def test_literal_output(self, text):
        assert _plain(text) == text

    @pytest.mark.parametrize("text,expected", [
        # ``\(`` 无 ``\)`` 配对时回退为反斜杠转义（CommonMark：``\(`` → ``(``）
        (r"\(" * 50, "(" * 50),
        (r"\(x" * 100, "(x" * 100),
        (r"\[" * 50, "[" * 50),
    ])
    def test_unclosed_falls_back(self, text, expected):
        assert _plain(text) == expected

    def test_has_char_guard_short_text(self):
        """短文本（无索引）走 ``in`` 回退路径，判定一致。"""
        p = _InlineParser("abc[def")
        assert p._has_char("[") is True and p._has_char("]") is False

    def test_has_char_guard_long_text(self):
        p = _InlineParser("[" * 400)
        assert p._has_char("]") is False
        assert p._has_char("[") is True

    def test_has_seq_cached(self):
        p = _InlineParser("a]]b" * 100)
        assert p._has_seq("]]") is True
        assert p._has_seq("--)") is False
        # 二次调用命中缓存，结果一致
        assert p._has_seq("]]") is True


class TestMarkupStillRenders:
    """优化路径下的常规语法渲染（行为锁定）。"""

    @pytest.mark.parametrize("text,plain", [
        ("&amp;", "&"),
        ("[a](http://x)", "a"),
        ("[[wiki]]", "wiki"),
        ("[[wiki|W]]", "W"),
        ("[^1]", "[^1]"),
        (r"\(x\)", "x"),
        ("<a href=\"u\">y</a>", "y"),
        ("<b>b</b>", "b"),
        ("<span>s</span>", "s"),
        ("*em*", "em"),
        ("**st**", "st"),
        ("_em_", "em"),
        ("<br>", "\n"),
        ("^[note]", "[^note]"),
        ("x^2^", "x²"),
    ])
    def test_plain_output(self, text, plain):
        assert _plain(text) == plain

    def test_styles_preserved(self):
        assert _runs("**st**") == [("st", None, True, False)]
        assert _runs("*em*") == [("em", None, False, True)]
        assert _runs("[[wiki]]") == [("wiki", 201, False, False)]
        assert _runs("[a](http://x)") == [("a", 45, False, False)]

    def test_unclosed_kept_literal_inline(self):
        assert _plain("[abc") == "[abc"
        assert _plain("&amp") == "&amp"
        assert _plain(r"\(x") == "(x"
        assert _plain("<a") == "<a"
        assert _plain("[^1") == "[^1"

    def test_long_tag_name_not_swallowed(self):
        """超长「标签名」不命中标签表 → 原样输出（不吞内容）。"""
        text = "<" + "a" * 200 + ">x"
        assert _plain(text) == text

    def test_html_content_with_close_tag(self):
        assert _plain("<a href=\"u\">" + "y" * 500 + "</a>") == "y" * 500

    def test_escaped_paren_in_paren_math(self):
        # 行内数学内 ``\(`` 的转义与闭合定位仍正确
        assert _plain(r"\(a\)x\(b\)") == "axb"

    def test_wikilink_with_pipe_and_nested_bracket(self):
        assert _plain("[[target|display]] and [[plain]]") == "display and plain"

    def test_footnote_ref_with_space_boundary(self):
        assert _plain("[^a] tail") == "[^a] tail"


# ═══════════════════════════════════════════════════════════
# 3. 超长单行性能（线性度 + 宽松上界）
# ═══════════════════════════════════════════════════════════

#: 单字符（或双字符）run 重复输入——修复前这些是 O(n²)（2000 字符 0.9~1.6s）。
_RUN_PATTERNS = [
    ("*", 8000),
    ("_", 8000),
    ("+", 8000),
    ("|", 8000),
    ("&", 8000),
    ("[", 8000),
    ("<a", 8000),
    (r"\(", 8000),
    ("[^", 8000),
    ("=", 8000),
]


class TestLongRunPerformance:
    @pytest.mark.parametrize("pattern,size", _RUN_PATTERNS)
    def test_long_run_linear(self, pattern, size):
        """重复定界符 run：耗时随长度近线性（修复前为平方退化）。"""
        small = pattern * (size // 8 // len(pattern))
        big = pattern * (size // len(pattern))
        t_small = _elapsed(lambda: render_inline(small), repeats=2)
        t_big = _elapsed(lambda: render_inline(big), repeats=2)
        # 8 倍长度：平方退化会是 64 倍；宽松上限 16 倍（含噪声余量）
        assert t_big < max(t_small * 16, 0.35), (pattern, t_small, t_big)

    @pytest.mark.parametrize("pattern,size", _RUN_PATTERNS)
    def test_long_run_budget(self, pattern, size):
        text = pattern * (size // len(pattern))
        assert _elapsed(lambda: render_inline(text), repeats=2) < 1.0

    def test_long_line_mixed_markup_budget(self):
        text = "**a** `b` [c](d) &amp; <b>x</b> " * 300
        assert _elapsed(lambda: render_inline(text), repeats=2) < 1.0

    def test_long_line_still_renders_all_content(self):
        """性能路径下内容不丢（超长 run 的字符数守恒）。"""
        text = "*" * 8000
        assert _plain(text) == text


# ═══════════════════════════════════════════════════════════
# 4. 流式渲染器整链路（write + 预览）
# ═══════════════════════════════════════════════════════════


class TestStreamLongLineEndToEnd:
    @staticmethod
    def _stream(text: str, chunk: int = 8, width: int = 120):
        from src.renderer.ansi import AnsiStreamRenderer
        r = AnsiStreamRenderer(width=width)
        for i in range(0, len(text), chunk):
            r.write(text[i:i + chunk])
            r.take_preview_lines()
        r.close()
        return [ln.plain for ln in r.take_lines()]

    @pytest.mark.parametrize("text", [
        "[" * 4000,
        "&" * 4000,
        r"\(" * 2000,
        "<a" * 2000,
        "x" + "*" * 4000,
        "[^" * 2000,
    ])
    def test_stream_path_budget(self, text):
        assert _elapsed(lambda: self._stream(text), repeats=1) < 2.0

    def test_stream_content_preserved(self):
        # 行首加普通字符——避免 ``****…`` 被块级识别为分隔线（hr）
        lines = self._stream("x" + "*" * 4000 + "\n\n")
        joined = "".join(lines)
        assert joined.count("*") == 4000

    def test_stream_bracket_run_no_preview_leak(self):
        """无闭合标记的病态行在预览/提交路径都原样保留（不丢字符）。"""
        text = "[" * 3000
        lines = self._stream(text + "\n\n")
        assert "".join(lines).count("[") == 3000
