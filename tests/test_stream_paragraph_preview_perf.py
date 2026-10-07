"""段落流式预览性能 / 一致性回归（ansi 层）。

背景：``AnsiStreamRenderer._render_paragraph_preview`` 修复前对「多行且含任一
行内标记起始字符」的段落**每帧整段重解析**——长段落流式（预览有界化导致头部
滑窗）累计 O(n²)，1200 行 / 12 万字符段落逐 16 字符写入实测 >150s。

修复引入：
  1. ``_line_delims.ParagraphBoundaryScanner``——增量跟踪段落完整行边界处是否
     存在未闭合行内开定界符，给出「可安全逐行渲染的完整行数」（保守：多报
     未闭合，不漏报）；
  2. ``_line_match``——行列表的前缀 / 头部滑窗重叠检测（预览有界化会丢弃最旧
     行，纯前缀复用完全失效）；
  3. ``_preview_cache.LinePreviewCache`` 滑窗复用（忽略活动行）；
  4. ``_render_paragraph_preview`` 稳定前缀逐行增量渲染 + 未闭合尾部整段解析。

本文件固化上述语义等价性与性能边界。
"""
from __future__ import annotations

import random

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi._line_delims import ParagraphBoundaryScanner
from src.renderer.ansi._line_match import common_prefix_len, sliding_drop
from src.renderer.ansi._preview_cache import LinePreviewCache
from src.renderer.ansi import blocks as _blocks
from src.renderer.ansi.helpers import AnsiLine
from src.renderer.types import Token, TokenType


def _committed_paragraph(content: str) -> list[str]:
    """段落提交路径渲染结果（plain 行）。"""
    return [l.plain for l in _blocks.render_paragraph(
        Token(TokenType.PARAGRAPH, content))]


def _preview_streaming(content: str, width: int = 80) -> list[str]:
    """按字符分块流式写入单个段落，返回末帧预览 plain 行。"""
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(content), 3):
        r.write(content[i:i + 3])
        r.take_preview_lines()
    return [l.plain for l in r.take_preview_lines()]


# ═══════════════════════════════════════════════════════════
# 行匹配工具
# ═══════════════════════════════════════════════════════════


class TestLineMatch:
    def test_common_prefix_len(self):
        assert common_prefix_len(["a", "b", "c"], ["a", "b", "d"]) == 2
        assert common_prefix_len([], ["a"]) == 0
        assert common_prefix_len(["a"], []) == 0
        assert common_prefix_len(["a"], ["a"]) == 1

    def test_sliding_drop_detects_head_window(self):
        a = [f"l{i}" for i in range(10)]
        b = [f"l{i}" for i in range(2, 12)]
        assert sliding_drop(a, b) == 2

    def test_sliding_drop_no_overlap(self):
        a = ["x", "y", "z"]
        b = ["p", "q", "r"]
        assert sliding_drop(a, b) == 0

    def test_sliding_drop_ignore_tail(self):
        # 仅尾行不同（活动行）→ 忽略后仍能识别头部滑窗
        a = ["l0", "l1", "l2", "act-old"]
        b = ["l1", "l2", "l3", "act-new"]
        assert sliding_drop(a, b) == 0            # 含尾行比较 → 不匹配
        assert sliding_drop(a, b, ignore_tail=True) == 1

    def test_sliding_drop_short_lists(self):
        assert sliding_drop([], ["a"]) == 0
        assert sliding_drop(["a"], []) == 0
        assert sliding_drop(["a"], ["b"]) == 0


# ═══════════════════════════════════════════════════════════
# 行边界定界符扫描
# ═══════════════════════════════════════════════════════════


class TestParagraphBoundaryScanner:
    def _stable(self, text: str) -> int:
        return ParagraphBoundaryScanner().stable_line_count(text)

    def test_all_lines_stable(self):
        text = "第一行\n第二行\n第三行"
        assert self._stable(text) == 2          # 完整行 = 2（第三行是活动行）

    def test_unclosed_bold_spans_lines(self):
        assert self._stable("**粗体\n跨行**") == 0

    def test_closed_bold_per_line_stable(self):
        assert self._stable("a **b**\nc **d**\n") == 2

    def test_unclosed_italic_single_star(self):
        # 单个 `*` 无论 flanking 均可能跨行配对 → 保守判未闭合
        assert self._stable("a * b\nc * d\n") == 0

    def test_snake_case_underscore_not_delimiter(self):
        assert self._stable("snake_case\nfoo_bar\n") == 2

    def test_bold_underscore_pairs(self):
        assert self._stable("__bold__\nplain\n") == 2

    def test_inline_code_closed(self):
        assert self._stable("a `code` b\nnext\n") == 2

    def test_inline_code_unclosed(self):
        assert self._stable("a `code\nnext\n") == 0

    def test_link_closed_vs_open_paren(self):
        assert self._stable("see [t](http://x) ok\nnext\n") == 2
        assert self._stable("see [t](\nhttp://x)\n") == 0

    def test_bracket_unclosed(self):
        assert self._stable("[t\n](x)\n") == 0

    def test_strikethrough_and_subscript(self):
        assert self._stable("~~a~~\nnext\n") == 2
        assert self._stable("~~a\nb~~\n") == 0

    def test_highlight_needs_double_equals(self):
        # 单个 `=` 不构成高亮 → 不影响稳定
        assert self._stable("x = 1\ny = 2\n") == 2
        assert self._stable("a ==b\nc==\n") == 0

    def test_math_dollar(self):
        assert self._stable("$x$ ok\nnext\n") == 2
        assert self._stable("$x\n\n") == 0

    def test_escape_paren_math(self):
        assert self._stable("\\(x\\) ok\nnext\n") == 2
        assert self._stable("\\(x\nnext\n") == 0

    def test_html_tag_unclosed(self):
        assert self._stable("<div\nclass='x'>\n") == 0

    def test_plain_less_than_not_tag(self):
        # `a < b` 不构成 HTML 标签 → 不影响稳定
        assert self._stable("a < b\nc > d\n") == 2

    def test_critic_and_color_brace(self):
        assert self._stable("{++a++}\nnext\n") == 2
        assert self._stable("{++a\nnext\n") == 0
        assert self._stable("{color:red}x{color}\nnext\n") == 2

    def test_mixed_specifiers_separate_counts(self):
        # `**`（粗体）与 `*`（斜体）分开计数 → 各自未闭合都能识别
        assert self._stable("**a*\n**b**\n") == 0

    def test_incremental_append(self):
        s = ParagraphBoundaryScanner()
        assert s.stable_line_count("第一行\n") == 1
        assert s.stable_line_count("第一行\n第二行\n") == 2
        assert s.stable_line_count("第一行\n第二行\n第三行") == 2

    def test_incremental_head_sliding(self):
        s = ParagraphBoundaryScanner()
        lines = [f"行{i}\n" for i in range(60)]
        for i in range(1, len(lines) + 1):
            s.stable_line_count("".join(lines[:i]))
        # 头部滑窗：丢掉最旧 10 行
        assert s.stable_line_count("".join(lines[10:])) == 50

    def test_unstable_then_stable_keeps_first_unstable(self):
        # 前两行构成跨行标记 → 稳定前缀只能是 0（即便后续行"恢复"）
        s = ParagraphBoundaryScanner()
        assert s.stable_line_count("**a\nb**\nnext\n") == 0

    def test_reset(self):
        s = ParagraphBoundaryScanner()
        s.stable_line_count("a\nb\n")
        s.reset()
        assert s.stable_line_count("c\nd\n") == 2

    def test_empty_text(self):
        s = ParagraphBoundaryScanner()
        assert s.stable_line_count("") == 0

    def test_fuzz_no_crash_and_monotone(self):
        """任意输入不抛异常；稳定前缀随行增加单调不减（同一前缀）。"""
        rnd = random.Random(20261007)
        alphabet = list("ab *_~=`[]()<>{}|^$+%\\\n中")
        for _ in range(100):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 120)))
            s = ParagraphBoundaryScanner()
            assert s.stable_line_count(text) >= 0


# ═══════════════════════════════════════════════════════════
# LinePreviewCache 滑窗复用
# ═══════════════════════════════════════════════════════════


class TestLinePreviewCacheSliding:
    @staticmethod
    def _rl(text):
        return [AnsiLine.of(text)]

    def test_prefix_reuse(self):
        c = LinePreviewCache()
        r1 = c.render(("p",), ["a", "b"], self._rl)
        r2 = c.render(("p",), ["a", "b", "c"], self._rl)
        assert r2[0] is r1[0]
        assert r2[1] is r1[1]
        assert r2[2].plain == "c"

    def test_sliding_reuse(self):
        c = LinePreviewCache()
        r1 = c.render(("p",), ["a", "b", "c"], self._rl)
        b_row = r1[1]
        c_row = r1[2]
        r2 = c.render(("p",), ["b", "c", "d"], self._rl)
        assert r2[0] is b_row
        assert r2[1] is c_row
        assert r2[2].plain == "d"

    def test_sliding_ignore_active_tail(self):
        """活动行（尾行）变化不影响头部滑窗复用。"""
        c = LinePreviewCache()
        r1 = c.render(("p",), ["a", "b", "active-old"], self._rl)
        mid = r1[1]
        r2 = c.render(("p",), ["b", "c", "active-new"], self._rl)
        assert r2[0] is mid

    def test_key_change_resets(self):
        c = LinePreviewCache()
        r1 = c.render(("p",), ["a"], self._rl)
        r2 = c.render(("q",), ["a"], self._rl)
        assert r2[0] is not r1[0]


# ═══════════════════════════════════════════════════════════
# 段落预览与提交一致性（跨软换行标记）
# ═══════════════════════════════════════════════════════════


class TestParagraphPreviewConsistency:
    def test_cross_line_marks_match_committed(self):
        for content in (
            "**粗体\n跨行**",
            "*斜体\n跨行*",
            "`code\ncode`",
            "~~删除\n线~~",
            "==高亮\n跨行==",
            "**a**\nb **c**",
            "**a\nb** c **d**",
            "a * b\nc * d",
            "$x\ny$",
            "plain\nlines\nhere",
            "snake_case\nfoo_bar",
        ):
            prev = _preview_streaming(content)
            assert prev == _committed_paragraph(content), content

    def test_unclosed_mark_visible_before_close(self):
        """未闭合标记在流式中途原样呈现，闭合后配对（不丢内容）。"""
        r = AnsiStreamRenderer(width=80)
        r.write("**粗体\n")
        mid = [l.plain for l in r.take_preview_lines()]
        assert any("**" in p or "粗体" in p for p in mid)
        r.write("跨行**\n\n")
        done = [l.plain for l in r.take_lines()]
        assert "".join(done) == "粗体跨行"

    def test_long_paragraph_no_leak(self):
        """含行内标记的长段落逐行流式：内容不泄漏标记、行数正确。"""
        lines = [f"第{i}行 **粗体** 与 `code{i}`" for i in range(40)]
        content = "\n".join(lines)
        prev = _preview_streaming(content)
        assert prev == _committed_paragraph(content)
        assert len(prev) == 40


# ═══════════════════════════════════════════════════════════
# 性能边界：逐行渲染次数近线性
# ═══════════════════════════════════════════════════════════


class TestParagraphPreviewPerf:
    def test_incremental_line_render_linear(self, monkeypatch):
        """N 行段落流式预览：单行渲染次数 ~O(N)，而非 O(N²)。"""
        calls = {"n": 0}
        orig = _blocks.render_paragraph_line

        def spy(text):
            calls["n"] += 1
            return orig(text)

        monkeypatch.setattr(_blocks, "render_paragraph_line", spy)
        r = AnsiStreamRenderer(width=100)
        n = 300
        content = "".join(
            f"第{i}行 **粗体** 与链接[文本](http://x/{i})。\n" for i in range(n)
        )
        for i in range(0, len(content), 16):
            r.write(content[i:i + 16])
            r.take_preview_lines()
        # 全量重渲染约 N×帧数（数万）；增量应接近 N（含少量滑窗边界重渲）
        assert calls["n"] < n * 6, calls["n"]

    def test_markdown_paragraph_no_full_reparse(self, monkeypatch):
        """无跨行标记的段落不触发整段重解析（render_paragraph 调用极少）。"""
        calls = {"n": 0}
        orig = _blocks.render_paragraph

        def spy(token):
            calls["n"] += 1
            return orig(token)

        monkeypatch.setattr(_blocks, "render_paragraph", spy)
        r = AnsiStreamRenderer(width=100)
        content = "".join(f"第{i}行 **粗体** 与 `code{i}`。\n" for i in range(200))
        for i in range(0, len(content), 16):
            r.write(content[i:i + 16])
            r.take_preview_lines()
        assert calls["n"] < 20, calls["n"]

    def test_cross_line_mark_still_full_parse(self, monkeypatch):
        """存在跨行未闭合标记时限内回退整段解析（保证一致性）。"""
        calls = {"n": 0}
        orig = _blocks.render_paragraph

        def spy(token):
            calls["n"] += 1
            return orig(token)

        monkeypatch.setattr(_blocks, "render_paragraph", spy)
        r = AnsiStreamRenderer(width=100)
        r.write("**未闭合\n")
        for _ in range(30):
            r.write("继续内容\n")
            r.take_preview_lines()
        assert calls["n"] >= 10


# ═══════════════════════════════════════════════════════════
# 代码块单行高亮缓存
# ═══════════════════════════════════════════════════════════


class TestCodeHighlightLineCache:
    @staticmethod
    def _reset():
        from src.renderer.ansi import code as _code
        _code._LINE_HIGHLIGHT_CACHE.clear()
        return _code

    def test_repeated_line_reuses_highlight_object(self):
        code = self._reset()
        rows = code.highlight_code_lines(["x = 1", "x = 1"], "python", "monokai")
        assert rows[0] is rows[1]

    def test_distinct_lines_not_shared(self):
        code = self._reset()
        rows = code.highlight_code_lines(["x = 1", "y = 2"], "python", "monokai")
        assert rows[0] is not rows[1]

    def test_cache_preserves_text(self):
        code = self._reset()
        first = code.highlight_code_lines(["x = 1"], "python", "monokai")[0]
        second = code.highlight_code_lines(["x = 1"], "python", "monokai")[0]
        assert first is second
        assert first.plain == "x = 1"

    def test_cache_bounded(self):
        code = self._reset()
        limit = code._LINE_HIGHLIGHT_CACHE_MAX
        for i in range(limit + 50):
            code.highlight_code_lines([f"v{i} = {i}"], "python", "monokai")
        assert len(code._LINE_HIGHLIGHT_CACHE) <= limit

    def test_highlight_row_not_cached(self):
        """高亮行（叠加标记）为新建对象，不污染缓存内容。"""
        code = self._reset()
        plain = code.highlight_code_lines(["x = 1"], "python", "monokai")[0]
        marked = code.highlight_code_lines(
            ["x = 1"], "python", "monokai", highlight_lines=[1])[0]
        assert marked is not plain
        assert marked.plain == "\u25b8 " + plain.plain
        # 缓存仍为未加标记的原始行
        again = code.highlight_code_lines(["x = 1"], "python", "monokai")[0]
        assert again is plain

    def test_plain_text_lang_no_lexer(self):
        code = self._reset()
        rows = code.highlight_code_lines(["a", "a"], "text", "monokai")
        assert rows[0].plain == rows[1].plain == "a"
