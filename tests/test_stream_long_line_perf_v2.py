"""流式超长单行渲染性能优化（第二批）测试。

本轮改动（`tui 流式 markdown 优化超长单行文本渲染性能`）的正确性/等价性
与性能边界固化：

1. ``src._text_width.string_width``——长文本（≥ ``_STRING_WIDTH_COUNTER_MIN``）
   且无 ESC 时走「``Counter`` 计数 + 每唯一字符一次 ``char_width``」路径，
   与逐字符路径**严格等价**；
2. ``renderer.ansi.helpers``——``wrap_line`` 等宽快路径改为「段切片 + bisect」
   发射（``_run_spans`` / ``_emit_span_range``），免逐字符构造与逐字符样式
   比较；宽 1 非 ASCII 行（café / 希腊字母）同样命中快路径；产出的 Run 预置
   显示宽度缓存（``Run._w``），下游免重算；与逐字符参考实现完全等价；
3. ``renderer._block_parser``——超长活动行的**延迟拼接**（``_TAIL_LAG_MIN`` /
   ``_TAIL_JOIN_STEP``）：无换行增量先入分块列表、按步长并入主缓冲，消除
   ``self._buffer += text`` 在超长未换行尾部下的累计 O(n²)；内容不丢、顺序
   正确、提交（``flush``/换行）前必合并，流式提交产出于一次性渲染一致。
"""

from __future__ import annotations

import random

from src._text_width import (
    _STRING_WIDTH_COUNTER_MIN,
    char_width,
    skip_ansi_at,
    string_width,
)
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import (
    AnsiLine,
    Run,
    _all_width_one,
    _emit_span_range,
    _run_spans,
    wrap_line,
)
from src.renderer.ansi.style import Style
from src.renderer._utils import cjk_display_width
from src.renderer._block_parser import RegexFreeBlockParser as RecursiveDescentParser


# ═══════════════════════════════════════════════════════════
# 参考实现
# ═══════════════════════════════════════════════════════════


def _naive_string_width(text: str) -> int:
    """逐字符参考实现（ANSI 序列整段 0 宽，控制字符 0 宽）。"""
    total = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            i = skip_ansi_at(text, i)
            continue
        total += char_width(ch)
        i += 1
    return total


def _wrap_reference(line: AnsiLine, max_width: int) -> list[AnsiLine]:
    """逐字符 tuple 展开 + 贪心填充的参考换行实现（通用路径语义）。"""
    if max_width <= 0:
        return [line] if line.runs else []
    items: list[tuple[str, Style | None, str | None]] = []
    for run in line.runs:
        for ch in run.text:
            items.append((ch, run.style, run.link))
    n = len(items)
    if n == 0:
        return []
    lines: list[AnsiLine] = []
    i = 0
    while i < n:
        j = i
        width = 0
        last_space = -1
        while j < n:
            ch = items[j][0]
            if ch == "\n":
                break
            if ch == " ":
                last_space = j
            cw = cjk_display_width(ch)
            if width + cw > max_width and j > i:
                break
            width += cw
            j += 1
        if j == i:
            if items[i][0] == "\n":
                lines.append(AnsiLine())
                i += 1
                continue
            end = i + 1
            next_i = i + 1
        elif j < n and items[j][0] == "\n":
            end = j
            next_i = j + 1
        elif j < n and last_space > i:
            end = last_space
            next_i = last_space + 1
        else:
            end = j
            next_i = j
        out = AnsiLine()
        seg_style = items[i][1] if i < end else None
        seg_link = items[i][2] if i < end else None
        buf: list[str] = []
        for k in range(i, end):
            ch, st, lk = items[k]
            if st != seg_style or lk != seg_link:
                if buf:
                    out.append("".join(buf), seg_style, seg_link)
                    buf = []
                seg_style = st
                seg_link = lk
            buf.append(ch)
        if buf:
            out.append("".join(buf), seg_style, seg_link)
        if out.runs:
            lines.append(out)
        i = next_i
    return lines


def _shape(lines) -> list:
    return [[(r.text, r.style, r.link) for r in ln.runs] for ln in lines]


# ═══════════════════════════════════════════════════════════
# 1. string_width 计数快速路径
# ═══════════════════════════════════════════════════════════


class TestStringWidthCounterPath:
    CASES = [
        "中文内容" * 64,                       # 长 CJK（宽 2）
        "lorem ipsum dolor sit amet " * 8,     # 长 ASCII（isascii 快路径）
        "mixed 中文 abc " * 32,                # 混排
        "caf\u00e9 r\u00e9sum\u00e9 " * 16,     # 宽 1 非 ASCII
        "emoji \U0001f600\U0001f601 x " * 16,  # emoji（宽 2）
        "\u4e2d\u6587\t\u4e2d\u6587 " * 32,     # 含控制字符（\t 宽 0）
        "abc\x1b[31mred\x1b[0m" * 16,          # 含 ANSI（回退逐字符）
        "a" * 65,                              # 略超阈值
        "文" * 64,                             # 恰好阈值
        "",                                    # 空串
    ]

    def test_matches_naive(self):
        for text in self.CASES:
            assert string_width(text) == _naive_string_width(text), repr(text[:20])

    def test_matches_forced_slow_path(self, monkeypatch):
        """强行关闭计数路径（阈值抬高）结果不变。"""
        import src._text_width as tw

        fast = [string_width(t) for t in self.CASES]
        monkeypatch.setattr(tw, "_STRING_WIDTH_COUNTER_MIN", 1 << 30)
        slow = [string_width(t) for t in self.CASES]
        assert fast == slow

    def test_counter_threshold_boundary(self):
        assert _STRING_WIDTH_COUNTER_MIN > 0
        short = "中文" * 10
        assert len(short) < _STRING_WIDTH_COUNTER_MIN
        assert string_width(short) == 40

    def test_fuzz_matches_naive(self):
        rnd = random.Random(20261012)
        alphabet = ["a", " ", "中", "文", "\U0001f600", "\u00e9", "\t", "x",
                    "0", "\u0301"]
        for _ in range(200):
            text = "".join(rnd.choice(alphabet)
                           for _ in range(rnd.randint(0, 200)))
            assert string_width(text) == _naive_string_width(text), repr(text[:30])

    def test_ansi_sequence_zero_width(self):
        assert string_width("\x1b[31m" * 40) == 0
        assert string_width("\x1b[31mred\x1b[0m" * 8) == 24


# ═══════════════════════════════════════════════════════════
# 2. wrap_line 段切片快路径
# ═══════════════════════════════════════════════════════════


class TestWrapLineSpanFastPath:
    WIDTHS = [1, 2, 3, 5, 8, 12, 40, 120]

    def test_cjk_uniform_equivalent(self):
        line = AnsiLine([Run("中文内容" * 200, None)])
        for w in self.WIDTHS:
            assert _shape(wrap_line(line, w)) == _shape(_wrap_reference(line, w))

    def test_plain_ascii_equivalent(self):
        line = AnsiLine([Run("hello world " * 200, None)])
        for w in self.WIDTHS:
            assert _shape(wrap_line(line, w)) == _shape(_wrap_reference(line, w))

    def test_width_one_non_ascii_uses_fast_path(self):
        """宽 1 非 ASCII（café / 希腊字母 / 西里尔）同样命中等宽快路径。"""
        line = AnsiLine([Run("caf\u00e9 r\u00e9sum\u00e9 \u03b1\u03b2\u03b3 " * 40, None)])
        assert _all_width_one(line.runs)
        for w in self.WIDTHS:
            assert _shape(wrap_line(line, w)) == _shape(_wrap_reference(line, w))

    def test_multi_run_uniform_equivalent(self):
        line = AnsiLine([
            Run("中" * 50, Style(fg=1)),
            Run("文内容" * 50, Style(fg=2)),
            Run("尾巴" * 30, None),
        ])
        for w in self.WIDTHS:
            assert _shape(wrap_line(line, w)) == _shape(_wrap_reference(line, w))

    def test_width_one_with_links_and_styles(self):
        line = AnsiLine([
            Run("abc def ", None, "http://a"),
            Run("ghi jkl " * 20, Style(fg=3), "http://b"),
        ])
        for w in self.WIDTHS:
            assert _shape(wrap_line(line, w)) == _shape(_wrap_reference(line, w))

    def test_run_width_cache_prefilled(self):
        """等宽快路径产出的 Run 预置 ``_w``（下游免重算），且值正确。"""
        line = AnsiLine([Run("中文内容" * 100, None)])
        for wrapped in wrap_line(line, 12):
            for run in wrapped.runs:
                assert run._w >= 0, "宽度应已预置"
                assert run._w == cjk_display_width(run.text)

    def test_all_width_one_predicate(self):
        assert _all_width_one([Run("abc def", None)])
        assert _all_width_one([Run("caf\u00e9", None)])
        assert _all_width_one([Run("a\tb", None)])              # \t 展开为空格（宽 1）
        assert not _all_width_one([Run("中文", None)])
        assert not _all_width_one([Run("a\x01b", None)])        # 控制字符宽 0
        assert not _all_width_one([Run("a\u0301b", None)])      # 组合标记宽 0
        assert not _all_width_one([Run("a\U0001f600", None)])   # emoji 宽 2

    def test_fuzz_equivalent(self):
        rnd = random.Random(20261012)
        alphabet = ["a", " ", "中", "文", "\U0001f600", "\u00e9", "*", "-"]
        for _ in range(200):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 80)))
            line = AnsiLine([Run(text, None)])
            w = rnd.randint(1, 20)
            assert _shape(wrap_line(line, w)) == _shape(_wrap_reference(line, w)), (
                text, w)


class TestSpanHelpers:
    def test_run_spans_layout(self):
        runs = [Run("ab", Style(fg=1)), Run("", None), Run("中文", Style(fg=2))]
        text, starts, ends, styles, links = _run_spans(runs)
        assert text == "ab中文"
        assert starts == [0, 2]
        assert ends == [2, 4]
        assert styles == [Style(fg=1), Style(fg=2)]
        assert links == [None, None]

    def test_emit_span_range_cross_segments(self):
        runs = [Run("ab", Style(fg=1)), Run("中文", Style(fg=2)),
                Run("cd", None)]
        text, starts, ends, styles, links = _run_spans(runs)
        line = AnsiLine()
        _emit_span_range(line, text, (starts, ends, styles, links), 1, 5, 2)
        assert line.plain == "b中文c"
        assert [r.style for r in line.runs] == [Style(fg=1), Style(fg=2), None]
        assert [r._w for r in line.runs] == [2, 4, 2]

    def test_emit_span_range_merges_adjacent_same_style(self):
        runs = [Run("ab", Style(fg=1)), Run("cd", Style(fg=1))]
        text, starts, ends, styles, links = _run_spans(runs)
        line = AnsiLine()
        _emit_span_range(line, text, (starts, ends, styles, links), 0, 4, 1)
        assert len(line.runs) == 1
        assert line.runs[0].text == "abcd"
        assert line.runs[0]._w == 4

    def test_emit_span_range_empty(self):
        line = AnsiLine()
        text, starts, ends, styles, links = _run_spans([Run("abc", None)])
        _emit_span_range(line, text, (starts, ends, styles, links), 2, 2, 1)
        assert line.runs == []


# ═══════════════════════════════════════════════════════════
# 3. 解析器超长活动行延迟拼接
# ═══════════════════════════════════════════════════════════


class TestParserTailLag:
    def test_lag_activated_for_long_unwrapped_tail(self):
        p = RecursiveDescentParser()
        chunk = "x" * 512
        for _ in range(64):                     # 32768 字符 > _TAIL_LAG_MIN
            p.feed(chunk)
        assert p._tail_len >= 0
        total_logical = len(p._buffer) + p._tail_len
        assert total_logical == 64 * 512

    def test_merge_tail_keeps_order_and_content(self):
        p = RecursiveDescentParser()
        chunk = "中" * 500
        for _ in range(80):
            p.feed(chunk)
        p._merge_tail()
        assert p._tail_parts == []
        assert p._tail_len == 0
        # 合并后主缓冲为完整未换行尾部（含此前已合并部分 + 剩余分块）
        assert len(p._buffer) == 80 * 500
        assert p._buffer == "中" * (80 * 500)

    def test_flush_merges_pending(self):
        p = RecursiveDescentParser()
        for _ in range(80):
            p.feed("中" * 500)
        tokens = p.flush()
        assert p._tail_parts == [] and p._tail_len == 0
        joined = "".join(t.content for t in tokens if t.content)
        assert "中" * (80 * 500) in joined.replace("\n", "")

    def test_short_tail_not_lagged(self):
        p = RecursiveDescentParser()
        p.feed("short line")
        assert p._tail_parts == []
        assert p._buffer == "short line"

    def test_newline_merges_pending_before_parse(self):
        p = RecursiveDescentParser()
        for _ in range(40):
            p.feed("x" * 512)
        p.feed("\n")
        # 换行到来 → 先合并再逐行解析 → 缓冲清空、无残留分块
        assert p._tail_parts == []
        assert p._tail_len == 0


class TestLongLineStreamConsistency:
    @staticmethod
    def _stream(text: str, chunk: int, width: int = 120) -> list[str]:
        r = AnsiStreamRenderer(width=width)
        for i in range(0, len(text), chunk):
            r.write(text[i:i + chunk])
            r.take_preview_lines()
        r.close()
        return [ln.plain for ln in r.take_lines()]

    @staticmethod
    def _batch(text: str, width: int = 120) -> list[str]:
        r = AnsiStreamRenderer(width=width)
        r.write(text)
        r.close()
        return [ln.plain for ln in r.take_lines()]

    def test_long_plain_line_stream_matches_batch(self):
        text = "中文内容" * 20_000 + "\n\n"
        assert self._stream(text, 64) == self._batch(text)

    def test_long_ascii_line_stream_matches_batch(self):
        text = "lorem ipsum dolor sit amet " * 2_000 + "\n\n"
        assert self._stream(text, 32) == self._batch(text)

    def test_long_line_content_not_lost(self):
        text = "中文内容" * 20_000
        r = AnsiStreamRenderer(width=120)
        for i in range(0, len(text), 64):
            r.write(text[i:i + 64])
        r.write("\n\n")
        r.close()
        joined = "".join(ln.plain for ln in r.take_lines())
        assert joined.count("中") == text.count("中")


# ═══════════════════════════════════════════════════════════
# 4. 性能边界（宽松阈值；防超线性退化回归）
# ═══════════════════════════════════════════════════════════


def _elapsed(fn, repeats: int = 3) -> float:
    import time

    fn()
    t0 = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - t0) / repeats


class TestLongLineBudget:
    def test_string_width_long_cjk_budget(self):
        text = "中文内容" * 1024
        assert _elapsed(lambda: string_width(text)) < 0.01

    def test_wrap_line_long_cjk_budget(self):
        line = AnsiLine([Run("中文内容" * 4096, None)])
        assert _elapsed(lambda: wrap_line(line, 120)) < 0.02

    def test_wrap_line_long_ascii_budget(self):
        line = AnsiLine([Run("lorem ipsum dolor " * 4096, None)])
        assert _elapsed(lambda: wrap_line(line, 120)) < 0.02

    def test_parser_long_line_scales_linearly(self):
        """超长未换行单行：解析耗时随长度近线性（消除累计 O(n²)）。"""
        import time

        def _run(n_chars: int) -> float:
            p = RecursiveDescentParser()
            chunk = "x" * 512
            t0 = time.perf_counter()
            for _ in range(n_chars // 512):
                p.feed(chunk)
            return time.perf_counter() - t0

        small = _run(100_000)
        big = _run(800_000)
        # 8 倍长度：耗时不应随之平方增长（宽松 20x 上限）
        assert big < max(small * 20, 1.0), (small, big)

    def test_stream_long_line_total_budget(self):
        from src.renderer.ansi import AnsiStreamRenderer

        def _run():
            r = AnsiStreamRenderer(width=120)
            for _ in range(2000):
                r.write("中文内容" * 16)
                r.take_preview_lines()
            r.close()

        assert _elapsed(_run) < 3.0
