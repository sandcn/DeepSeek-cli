"""流式超长单行渲染性能回归（renderer / ansi 层）。

背景：超长单行的流式预览每帧都要重渲染 + 重新换行尾部窗口，热路径上的
字符级工作（行内解析、显示宽度测量、按宽换行）此前存在多处超线性/高常数
实现，导致单帧成本随行长度线性甚至平方增长。本文件固化以下修复的
**语义等价性**与**性能边界**：

1. ``renderer.ansi.inline.render_inline``——普通文本段用 ``str.find`` 批量
   定位下一个标记（原逐字符 ``buf += c`` 为 O(n²)，且每字符两次
   ``startswith``）；``_merge`` 缓存样式合并；子串无标记时不递归。
2. ``renderer.ansi.helpers.wrap_line``——新增「不超宽直接返回」「等宽字符
   按固定步长切分」快路径；字符/样式展开用 C 级 ``list.extend``。
3. ``renderer.ansi.helpers.AnsiLine/Run`` 宽度缓存 + ``exceeds_width``
   提前退出；``renderer._utils.cjk_display_width`` 单字符走带缓存的
   ``char_width``。
4. ``src._text_width``——``char_width`` 有界缓存；``string_width`` 非 ASCII
   分支内联缓存查找（不再每字符触发正则 + 区间二分）。
"""
from __future__ import annotations

import random

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line
from src.renderer.ansi.inline import render_inline
from src.renderer.ansi.style import Style
from src.renderer._utils import cjk_display_width
from src._text_width import char_width, string_width


# ═══════════════════════════════════════════════════════════
# 参考实现（修复前语义，用于等价性比对）
# ═══════════════════════════════════════════════════════════


def _wrap_reference(line: AnsiLine, max_width: int) -> list[AnsiLine]:
    """逐字符 tuple 展开 + 贪心填充的参考换行实现（修复前算法）。"""
    if max_width <= 0:
        return [line] if line.runs else []
    items: list[tuple[str, Style | None]] = []
    for run in line.runs:
        for ch in run.text:
            items.append((ch, run.style))
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
            ch, _ = items[j]
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
        buf: list[str] = []
        for k in range(i, end):
            ch, st = items[k]
            if st != seg_style:
                if buf:
                    out.append("".join(buf), seg_style)
                    buf = []
                seg_style = st
            buf.append(ch)
        if buf:
            out.append("".join(buf), seg_style)
        if out.runs:
            lines.append(out)
        i = next_i
    return lines


def _render_inline_reference(text: str, base: Style) -> list[Run]:
    """逐字符累积的参考行内解析（修复前算法，用于等价性比对）。"""
    runs: list[Run] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "`":
            end = text.find("`", i + 1)
            if end != -1:
                runs.append(Run(text[i + 1:end], base.merge(Style(fg=46, bold=True))))
                i = end + 1
                continue
        if text.startswith("**", i) or text.startswith("__", i):
            opener = "**" if text.startswith("**", i) else "__"
            end = text.find(opener, i + 2)
            if end != -1:
                runs.extend(_render_inline_reference(
                    text[i + 2:end], base.merge(Style(bold=True))))
                i = end + 2
                continue
            runs.append(Run(opener, base))
            i += 2
            continue
        if text.startswith("~~", i):
            end = text.find("~~", i + 2)
            if end != -1:
                runs.append(Run(text[i + 2:end], base.merge(Style(dim=True))))
                i = end + 2
                continue
            runs.append(Run("~~", base))
            i += 2
            continue
        if ch in ("*", "_"):
            if i + 1 < n and text[i + 1] == ch:
                i += 1
                continue
            end = text.find(ch, i + 1)
            if end != -1:
                runs.extend(_render_inline_reference(
                    text[i + 1:end], base.merge(Style(italic=True))))
                i = end + 1
                continue
        if ch == "[":
            close_bracket = text.find("]", i + 1)
            if (close_bracket != -1 and close_bracket + 1 < n
                    and text[close_bracket + 1] == "("):
                close_paren = text.find(")", close_bracket + 2)
                if close_paren != -1:
                    runs.append(Run(
                        text[i + 1:close_bracket],
                        base.merge(Style(fg=45, underline=True)),
                    ))
                    i = close_paren + 1
                    continue
        if ch == "<":
            end = text.find(">", i + 1)
            if end != -1 and ("://" in text[i + 1:end]
                              or text[i + 1:end].startswith("mailto:")):
                runs.append(Run(text[i + 1:end], base.merge(Style(fg=45, underline=True))))
                i = end + 1
                continue
        j = i
        buf = ""
        while j < n:
            c = text[j]
            if c in ("*", "_", "`", "[", "<") or text.startswith("~~", j):
                break
            buf += c
            j += 1
        if buf:
            runs.append(Run(buf, base))
            i = j
            continue
        runs.append(Run(ch, base))
        i += 1
    return runs


def _runs_equal(a, b) -> bool:
    ra = [(r.text, r.style) for r in getattr(a, "runs", [])]
    rb = [(r.text, r.style) for r in getattr(b, "runs", [])]
    return ra == rb


# ═══════════════════════════════════════════════════════════
# wrap_line 快路径等价性
# ═══════════════════════════════════════════════════════════


_WRAP_CASES = [
    AnsiLine([Run("x" * 200, None)]),                          # 纯 ASCII 无空格
    AnsiLine([Run("hello world " * 20, None)]),                # ASCII 带空格
    AnsiLine([Run("中文内容" * 40, None)]),                     # 纯 CJK（等宽 2）
    AnsiLine([Run("中", Style(fg=1)), Run("文内容" * 30, Style(fg=2)),
              Run(" tail", None)]),                            # 多 run 混合
    AnsiLine([Run("a" * 5 + "\n" + "b" * 5, None)]),           # 强制换行
    AnsiLine([Run("\n\nabc\n", None)]),                        # 空行
    AnsiLine([Run("caf\u00e9 r\u00e9sum\u00e9 " * 10, None)]),  # 宽 1 非 ASCII
    AnsiLine([Run("emoji \U0001f600\U0001f601 x" * 10, None)]),  # 宽 2 emoji
    AnsiLine([Run("mixed 中文 abc 内容" * 15, None)]),          # 中英混合
    AnsiLine([Run("  leading spaces  " * 8, None)]),
    AnsiLine([Run("a", None), Run("", None)]),
    AnsiLine([Run("", None)]),
    AnsiLine(),
    AnsiLine([Run("x" * 50, Style(fg=1)), Run("y" * 50, Style(fg=1))]),
]

_WRAP_WIDTHS = [1, 2, 3, 5, 7, 8, 12, 40, 120, 1000]


class TestWrapLineEquivalence:
    def test_equivalent_to_reference(self):
        for line in _WRAP_CASES:
            for width in _WRAP_WIDTHS:
                got = wrap_line(line, width)
                exp = _wrap_reference(line, width)
                assert len(got) == len(exp), (
                    f"行数不一致 text={line.plain[:40]!r} width={width}")
                for g, e in zip(got, exp):
                    assert g.plain == e.plain
                    assert _runs_equal(g, e)
                    if width >= 2:
                        # 行宽上界（宽字符单字符超出行宽时按既有语义硬塞一个）
                        assert g.width <= width or len(g.plain) == 1

    def test_no_wrap_when_fits_returns_clone(self):
        line = AnsiLine([Run("short line", None)])
        out = wrap_line(line, 40)
        assert len(out) == 1
        assert out[0] is not line
        assert out[0].plain == "short line"

    def test_uniform_cjk_odd_width(self):
        line = AnsiLine([Run("中文内容测试文本", None)])
        assert [l.plain for l in wrap_line(line, 5)] == [
            l.plain for l in _wrap_reference(line, 5)]

    def test_random_fuzz_equivalence(self):
        rnd = random.Random(20261007)
        alphabet = ["a", "b", " ", "\u4e2d", "\u6587", "e\u0301", "\U0001f600",
                    "\u00e9", "*", "`"]
        for _ in range(150):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))
            style = None if rnd.random() < 0.5 else Style(fg=rnd.randint(1, 200))
            line = AnsiLine([Run(text, style)])
            width = rnd.randint(1, 20)
            got = wrap_line(line, width)
            exp = _wrap_reference(line, width)
            assert [l.plain for l in got] == [l.plain for l in exp]
            assert [_runs_equal(g, e) for g, e in zip(got, exp)] == [True] * len(exp)


# ═══════════════════════════════════════════════════════════
# 宽度缓存 / exceeds_width
# ═══════════════════════════════════════════════════════════


class TestWidthCache:
    def test_ansi_line_width_cache_invalidated_by_append(self):
        line = AnsiLine([Run("abc", None)])
        assert line.width == 3
        line.append("de")
        assert line.width == 5
        assert line.plain == "abcde"

    def test_run_width_matches_reference(self):
        for text in ("abc", "\u4e2d\u6587", "a\u4e2db", "\U0001f600", ""):
            assert Run(text, None).width == cjk_display_width(text)

    def test_exceeds_width_true_false(self):
        line = AnsiLine([Run("\u4e2d\u6587" * 10, None)])
        assert line.exceeds_width(39) is True
        assert line.exceeds_width(40) is False

    def test_exceeds_width_uses_cached_width(self):
        line = AnsiLine([Run("abc", None)])
        assert line.width == 3
        assert line.exceeds_width(2) is True
        assert line.exceeds_width(3) is False

    def test_exceeds_width_ascii_run(self):
        line = AnsiLine([Run("x" * 1000, None), Run("\u4e2d", None)])
        assert line.exceeds_width(1001) is True   # 1000 + 2 = 1002 > 1001
        assert line.exceeds_width(1002) is False

    def test_char_width_cache_consistent_with_bisect(self):
        from src._text_width import codepoint_width
        for ch in ("a", "\u4e2d", "\u00e9", "\U0001f600", " ", "\u0301"):
            assert char_width(ch) == codepoint_width(ord(ch)) or ch == "\u0301"
        # 缓存复用（同字符二次调用返回相同值）
        assert char_width("\u4e2d") == char_width("\u4e2d")

    def test_string_width_large_cjk(self):
        text = "\u4e2d\u6587\u5185\u5bb9" * 512  # 2048 字符
        assert string_width(text) == 4096
        assert string_width("abc") == 3


# ═══════════════════════════════════════════════════════════
# inline 解析等价性
# ═══════════════════════════════════════════════════════════

_INLINE_CASES = [
    "plain text",
    "**bold** and *italic*",
    "`code` and ~~strike~~",
    "[text](http://example.com)",
    "<https://example.com>",
    "**unclosed",
    "*unclosed",
    "~~unclosed",
    "a*b*c",
    "**a**b**c**",
    "***triple***",
    "__bold__ _italic_",
    "\u4e2d\u6587**\u7c97\u4f53**\u6587\u672c",
    "",
    "`code with **stars**`",
    "text with no markers at all " * 50,
    "**\u4e2d\u6587**",
    "~~gone~~[x](y)`z`<mailto:a@b.c>",
]


class TestInlineEquivalence:
    def test_equivalent_to_reference(self):
        base = Style()
        for text in _INLINE_CASES:
            got = render_inline(text)
            exp = _render_inline_reference(text, base)
            assert [(r.text, r.style) for r in got] == [(r.text, r.style) for r in exp], text

    def test_long_plain_text_single_run(self):
        text = "lorem ipsum dolor sit amet " * 500
        runs = render_inline(text)
        assert len(runs) == 1
        assert runs[0].text == text

    def test_bold_without_inner_markers_single_run(self):
        runs = render_inline("**bold**")
        assert len(runs) == 1
        assert runs[0].text == "bold"

    def test_random_fuzz_equivalence(self):
        rnd = random.Random(7)
        alphabet = ["a", "*", "_", "`", "~", "[", "]", "(", ")", "<", ">", " ",
                    "\u4e2d", "://", "mailto:"]
        base = Style()
        for _ in range(200):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 40)))
            got = render_inline(text)
            exp = _render_inline_reference(text, base)
            assert [(r.text, r.style) for r in got] == [(r.text, r.style) for r in exp], text


# ═══════════════════════════════════════════════════════════
# 流式渲染器：超长单行端到端（预览窗口有界 + 内容不丢）
# ═══════════════════════════════════════════════════════════


class TestLongLineStream:
    def test_long_single_line_preview_windowed(self):
        from src.renderer.ansi import _PREVIEW_MAX_LINE_CHARS
        r = AnsiStreamRenderer(width=120)
        for _ in range(20):
            r.write("x" * 500)
        lines = r.take_preview_lines()
        assert lines
        assert all(ln.width <= _PREVIEW_MAX_LINE_CHARS * 2 for ln in lines)

    def test_long_single_line_committed_intact(self):
        """超长单行闭合后提交内容完整（窗口化只作用于未闭合预览）。"""
        r = AnsiStreamRenderer(width=200)
        text = "\u4e2d\u6587\u5185\u5bb9" * 500
        r.write(text)
        r.write("\n\n")
        r.close()
        joined = "".join(ln.plain for ln in r.take_lines())
        assert joined.count("\u4e2d") == text.count("\u4e2d")

    def test_preview_src_lines_matches_split(self):
        """``_preview_src_lines`` 快速路径与「split + 末行窗口化」等价。"""
        from src.renderer.ansi import AnsiStreamRenderer, _PREVIEW_MAX_LINE_CHARS
        r = AnsiStreamRenderer(width=120)
        limit = _PREVIEW_MAX_LINE_CHARS
        cases = [
            "short",
            "multi\nline\ntext",
            "a" * (limit * 2),
            "head\n" + "b" * (limit * 2),
            "head\n" + "c" * (limit + 10) + "\ntail",
            ("x" * (limit + 5) + "\n") * 3,
        ]
        for content in cases:
            got = r._preview_src_lines(content)
            parts = content.split("\n")
            parts[-1] = r._window_preview_line(parts[-1])
            assert got == parts, content[:40]
