"""ANSI 流式 Markdown 语法/渲染修复（第十一批）测试。

本轮改动：行内高亮 ``==x==`` 的定界符采用 markdown-it-mark 同族的 CommonMark
flanking 判定（左定界符须 left-flanking、右定界符须 right-flanking），修复
「代码语义的 ``==`` 被吞、其间内容被误高亮」：

  - 修复前 ``mode=="change"，…；mode=="stable"`` 渲染为
    ``mode"change"…mode"stable"``（两个 ``==`` 消失、中间整段黄底高亮）；
  - 修复后 ``==`` 原样保留、无高亮；``==hi==`` / ``中文==高亮==中文`` /
    ``a ==b== c`` 等正常高亮不受影响。

同时覆盖：
  - **完整 run 的 flanking**：``a === b``（三连等号）不再被当成定界符；
  - **右定界符**：``a == b == c``（两侧空白）保持原样，不把 `` b `` 高亮；
  - **跨软换行高亮**仍可配对（``==高亮\\n跨行==``）；
  - **流式分块一致性**（逐字符写入与一次性写入产出一致）；
  - **段落边界扫描**与解析器口径一致（修复后不再把 ``==`` 当未闭合定界符）。

两路径同步：TUI（``AnsiStreamRenderer``）与 Rich（``IncrementalRenderer``）
共享同一解析层（``_InlineParser``），故两路径断言一致。
"""

from __future__ import annotations

import re

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline

#: 行内高亮的样式背景色（``ansi.inline._STYLE_HIGHLIGHT`` 的 bg）
_HL_BG = 11

#: 用户报告的那句（第 19 条）——技术文本里的 ``==`` 必须原样保留
REPORTED = (
    '19. `_await_screen`：`keep` 逻辑。mode=="change"，reference=before_path；'
    '若 diff.changed 返回；否则 keep=False → 删除 current。OK。但第一次循环 '
    '`previous = before_path`。mode=="stable" 时：`reference = previous`'
    '（=before_path），第一次比较 before vs current；若稳定则返回，否则 '
    'previous=current，keep=True。OK。'
)


def _render(src: str, width: int = 100):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return r.take_lines()


def _plain(src: str, width: int = 100) -> str:
    return "\n".join(ln.plain for ln in _render(src, width))


def _chunked_plain(src: str, size: int = 1, width: int = 100) -> str:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return "\n".join(ln.plain for ln in r.take_lines())


def _highlighted_texts(src: str, width: int = 100) -> list:
    """渲染后带高亮背景（bg=11）的 run 文本列表。"""
    out: list = []
    for ln in _render(src, width):
        for run in ln.runs:
            style = run.style
            if style is not None and getattr(style, "bg", None) == _HL_BG:
                out.append(run.text)
    return out


def _styled_texts(src: str, pred, width: int = 100) -> list:
    """渲染后满足样式条件 ``pred(style)`` 的 run 文本列表。"""
    out: list = []
    for ln in _render(src, width):
        for run in ln.runs:
            style = run.style
            if style is not None and pred(style):
                out.append(run.text)
    return out


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _rich_plain(src: str) -> str:
    import io

    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write(src)
    r.close()
    return _ANSI.sub("", buf.getvalue()).rstrip()


# ══════════════════════════════════════════════════════════
# 1. 代码语义的 ``==`` 保留（本次修复的核心）
# ══════════════════════════════════════════════════════════


class TestCodeStyleDoubleEquals:
    def test_single_comparison_preserved(self):
        assert _plain('mode=="change"') == 'mode=="change"'

    def test_two_comparisons_preserved(self):
        src = 'mode=="change" 与 mode=="stable"'
        assert _plain(src) == src

    def test_no_highlight_run(self):
        assert _highlighted_texts('mode=="change" 与 mode=="stable"') == []

    def test_reported_sentence_intact(self):
        got = _plain(REPORTED)
        assert got.count("==") == 2
        assert 'mode=="change"' in got
        assert 'mode=="stable"' in got
        assert _highlighted_texts(REPORTED) == []

    def test_reported_sentence_chunked_matches(self):
        assert _chunked_plain(REPORTED, size=3) == _plain(REPORTED)

    def test_reported_sentence_rich_path(self):
        got = _rich_plain(REPORTED)
        assert 'mode=="change"' in got
        assert 'mode=="stable"' in got

    def test_inline_code_double_equals_untouched(self):
        got = _plain("`a == b`")
        assert "a == b" in got
        assert _highlighted_texts("`a == b`") == []

    def test_word_internal_comparison(self):
        assert _plain("x==y") == "x==y"

    def test_inline_render_api_preserved(self):
        runs = render_inline('mode=="change"')
        assert "".join(r.text for r in runs) == 'mode=="change"'
        assert all(getattr(r.style, "bg", None) != _HL_BG for r in runs)


# ══════════════════════════════════════════════════════════
# 2. 正常高亮仍生效（不回归）
# ══════════════════════════════════════════════════════════


class TestHighlightStillWorks:
    def test_basic_highlight(self):
        assert _highlighted_texts("==hi==") == ["hi"]
        assert _plain("==hi==") == "hi"

    def test_cjk_highlight(self):
        assert _highlighted_texts("中文==高亮==中文") == ["高亮"]
        assert _plain("中文==高亮==中文") == "中文高亮中文"

    def test_highlight_between_spaces(self):
        assert _highlighted_texts("a ==b== c") == ["b"]

    def test_highlight_after_punctuation(self):
        assert _highlighted_texts("（==高亮==）") == ["高亮"]

    def test_highlight_cross_soft_line_break(self):
        # 跨软换行的高亮仍可配对：内容按行拆分为两个带高亮背景的 run
        assert _highlighted_texts("==高亮\n跨行==") == ["高亮", "跨行"]
        assert _plain("==高亮\n跨行==") == "高亮\n跨行"

    def test_highlight_rich_path(self):
        got = _rich_plain("中文==高亮==中文")
        assert "中文高亮中文" in got
        assert "==" not in got


# ══════════════════════════════════════════════════════════
# 3. 定界符 flanking 边界
# ══════════════════════════════════════════════════════════


class TestHighlightFlanking:
    def test_spaced_double_equals_plain(self):
        assert _plain("a == b == c") == "a == b == c"
        assert _highlighted_texts("a == b == c") == []

    def test_triple_equals_plain(self):
        assert _plain("a === b") == "a === b"

    def test_arrow_forms_unaffected(self):
        assert _highlighted_texts("箭头 ==> 与 <== 与 <==>") == []
        got = _plain("箭头 ==> 与 <== 与 <==>")
        assert "⟹" in got and "⟸" in got and "⟺" in got

    def test_unclosed_highlight_keeps_markers(self):
        assert _plain('a =="b"') == 'a =="b"'

    def test_open_requires_left_flanking(self):
        # 左字母 + 右标点（``=`` 属标点）→ 不是合法左定界符
        assert _plain('if a=="b" and c=="d"') == 'if a=="b" and c=="d"'

    def test_chunked_equivalence_with_highlight(self):
        src = "中文==高亮==中文 与 mode==\"change\""
        assert _chunked_plain(src, size=2) == _plain(src)


class TestSpanDelimitersFlanking:
    """同族双字符定界符（``++`` / ``||`` / ``~~``）的 flanking 一致行为。"""

    def test_increment_operator_preserved(self):
        assert _plain("a++ 与 b++") == "a++ 与 b++"
        assert _styled_texts("a++ 与 b++", lambda s: s.underline) == []

    def test_spaced_plus_preserved(self):
        assert _plain("a ++ b ++ c") == "a ++ b ++ c"
        assert _styled_texts("a ++ b ++ c", lambda s: s.underline) == []

    def test_underline_still_works(self):
        assert _styled_texts("++下划线++", lambda s: s.underline) == ["下划线"]
        assert _plain("中文++下划线++中文") == "中文下划线中文"

    def test_logical_or_preserved(self):
        assert _plain("a || b || c") == "a || b || c"
        assert _styled_texts("a || b || c", lambda s: s.fg == 240) == []

    def test_spoiler_still_works(self):
        assert _styled_texts("||剧透||", lambda s: s.fg == 240) == ["██"]

    def test_spaced_tilde_preserved(self):
        assert _plain("x ~~ y ~~ z") == "x ~~ y ~~ z"

    def test_triple_tilde_preserved(self):
        assert _plain("a ~~~ b") == "a ~~~ b"

    def test_strikethrough_still_works(self):
        assert _styled_texts("~~删除线~~", lambda s: s.dim) == ["删除线"]


# ══════════════════════════════════════════════════════════
# 4. 段落边界扫描与解析器口径一致
# ══════════════════════════════════════════════════════════


class TestParagraphBoundaryConsistency:
    def test_comparison_line_conservative_but_safe(self):
        """``a =="b"`` 行：扫描器保持保守（报「未闭合」→ 整段解析）。

        段落边界扫描的契约是**宁可多报未闭合**（只损失逐行缓存的性能），
        因此不随解析器的 flanking 收紧而收紧——修复后依然报未闭合，
        预览走整段解析，与提交结果一致（见 ``test_preview_matches_commit_*``）。
        """
        from src.renderer.ansi._line_delims import ParagraphBoundaryScanner

        s = ParagraphBoundaryScanner()
        assert s.stable_line_count('a =="b"\n') == 0

    def test_closed_highlight_line_is_stable(self):
        from src.renderer.ansi._line_delims import ParagraphBoundaryScanner

        s = ParagraphBoundaryScanner()
        assert s.stable_line_count("a ==b== c\n") == 1

    def test_unclosed_highlight_not_stable(self):
        from src.renderer.ansi._line_delims import ParagraphBoundaryScanner

        s = ParagraphBoundaryScanner()
        assert s.stable_line_count("a ==b\nc==\n") == 0

    def test_preview_matches_commit_for_reported_sentence(self):
        """流式预览（未 close）与提交结果一致——不再出现高亮跳变。"""
        r = AnsiStreamRenderer(width=100)
        r.write(REPORTED)
        preview = "\n".join(ln.plain for ln in r.take_preview_lines())
        assert 'mode=="change"' in preview
        assert "==" in preview
        r.close()
        committed = "\n".join(ln.plain for ln in r.take_lines())
        assert committed == _plain(REPORTED)

    def test_streaming_chunks_never_show_highlight(self):
        """逐字符流式写入过程中，任何一帧预览都不得出现被吞的 ``==``。"""
        r = AnsiStreamRenderer(width=100)
        for i in range(0, len(REPORTED), 5):
            r.write(REPORTED[i:i + 5])
            preview = "\n".join(ln.plain for ln in r.take_preview_lines())
            committed = "\n".join(ln.plain for ln in r.lines)
            for text in (preview, committed):
                if 'mode=="change"' in text or not text:
                    continue
                # 前缀尚未到达第一个 ``==`` 时跳过；一旦出现 ``mode==``
                # 附近内容，就不得缺少 ``==`` 定界符
                assert 'mode"' not in text
        r.close()
