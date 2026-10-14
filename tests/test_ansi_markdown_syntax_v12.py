"""ANSI 流式 Markdown 语法/渲染修复（第十二批）测试。

本轮改动：双字符行内定界符（``==`` 高亮 / ``~~`` 删除线 / ``||`` 剧透 /
``++`` 下划线）在 CommonMark flanking 之外追加「代码语义」判否（
``_InlineParser._span_delim_code_open`` / ``_span_delim_code_close``），修复
用户报告的「流式渲染这段 markdown 出错」：

  - 修复前 ``PickSpreadBlock(0)=="sculk_block"; (69)=="sculk_block"`` 渲染为
    ``PickSpreadBlock(0)"sculk_block"; (69)"sculk_block"``——两个 ``==`` 被吞、
    ``"sculk_block"; (69)`` 被整段误高亮；
  - 同类误判：``count==0; x==1``、``legacy==old, new==fresh``、
    ``assert x==y and z==w``、``A==B==C``、``x = (a==b), y = (c==d)``、
    ``a||b || c||d``、``a~~b, c~~d`` 等词内运算符 / 字符串字面量比较全部被吞；
  - 修复后这些 ``==`` / ``||`` / ``~~`` 原样保留、无样式泄漏；``==hi==`` /
    ``中文==高亮==中文`` / ``a ==b== c`` / ``（==高亮==）`` / 跨软换行高亮等
    正常语法不受影响。

判否口径（仅 ASCII，CJK 不是运算符语境）：

  - **开定界符**：紧邻左侧是 ASCII 标识符字符（``x==y``）；或紧随右侧是 ASCII
    引号（``(0)=="x"``）；
  - **闭定界符**：紧随右侧是 ASCII 标识符字符（``x==y, z==w``）；或紧邻左侧
    是 ASCII 引号。

两路径同步：TUI（``AnsiStreamRenderer``）与 Rich（``IncrementalRenderer``）
共享同一解析层（``_InlineParser``），故两路径断言一致；并固化「流式分块写入
与一次性写入产出一致」「预览任意帧都不得出现被吞的定界符」。
"""

from __future__ import annotations

import re

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline

#: 行内高亮的样式背景色（``ansi.inline._STYLE_HIGHLIGHT`` 的 bg）
_HL_BG = 11

#: 用户报告的内容（Minecraft sculk catalyst 实现笔记）——其中 ``==`` 是代码
#: 里的等于运算符，必须原样保留。
REPORTED = (
    'test_sculk_catalyst.cpp：\n'
    '- PickSpreadBlock(0)=="sculk_block"; (69)=="sculk_block"; '
    '(70)=="sculk_vein"; (89) vein? 权重 70+20=90 → roll<90 vein; '
    'then sensor (90..95), shrieker (96..99)\n'
    '- CanPlaceSolid\n'
    '- BloomsForXp(0)=0, (5)=5, (100)=24\n'
    '- 接线：onMobDeath 挂在 MinecraftAppWorld.cpp；'
    'UpdateSculkSpread 在 Update 调用\n'
)

#: 报告中必须原样保留的代码片段（各自含 ``==`` 的代码语义形态）
CODE_SNIPPETS = (
    'PickSpreadBlock(0)=="sculk_block"; (69)=="sculk_block"; (70)=="sculk_vein"',
    'count==0; x==1',
    'legacy==old, new==fresh',
    'assert x==y and z==w',
    'A==B==C',
    'x = (a==b), y = (c==d)',
    'a=="b"; c=="d"',
    '1==2, 3==4',
    'f(x)==y; f(z)==w',
    'a||b || c||d',
    'a~~b, c~~d',
)

#: 被吞后出现的形态（用于「流式任意帧不得出现」断言）
_EATEN_MARKERS = (
    'PickSpreadBlock(0)"',
    '(69)"',
    '(70)"',
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


def _inline_plain(src: str) -> str:
    return "".join(r.text for r in render_inline(src))


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
# 1. 用户报告内容（本次修复核心）
# ══════════════════════════════════════════════════════════


class TestReportedSculkBlock:
    def test_committed_keeps_all_delimiters(self):
        got = _plain(REPORTED)
        for snippet in ('PickSpreadBlock(0)=="sculk_block"',
                        '(69)=="sculk_block"',
                        '(70)=="sculk_vein"'):
            assert snippet in got, got

    def test_no_eaten_marker_in_committed(self):
        got = _plain(REPORTED)
        for bad in _EATEN_MARKERS:
            assert bad not in got

    def test_no_highlight_leak(self):
        assert _highlighted_texts(REPORTED) == []

    def test_list_structure_preserved(self):
        lines = _plain(REPORTED).split("\n")
        assert lines[0] == "test_sculk_catalyst.cpp："
        assert lines[1].startswith("• PickSpreadBlock(0)")
        assert lines[2] == "• CanPlaceSolid"
        assert lines[3] == "• BloomsForXp(0)=0, (5)=5, (100)=24"
        assert lines[4].startswith("• 接线：onMobDeath")

    def test_rest_of_reported_line_intact(self):
        got = _plain(REPORTED)
        assert "权重 70+20=90 → roll<90 vein" in got
        assert "then sensor (90..95), shrieker (96..99)" in got
        assert "UpdateSculkSpread 在 Update 调用" in got

    def test_chunked_equivalence(self):
        base = _plain(REPORTED)
        for size in (1, 2, 3, 5, 17):
            assert _chunked_plain(REPORTED, size=size) == base, size

    def test_streaming_frames_never_show_eaten_markers(self):
        """逐小块写入过程中，任何一帧都不得出现被吞的 ``==`` 形态。"""
        r = AnsiStreamRenderer(width=100)
        for i in range(0, len(REPORTED), 3):
            r.write(REPORTED[i:i + 3])
            chunks = [
                "\n".join(ln.plain for ln in r.lines),
                "\n".join(ln.plain for ln in r.take_preview_lines()),
            ]
            for text in chunks:
                for bad in _EATEN_MARKERS:
                    assert bad not in text, (i, bad, text)
        r.close()

    def test_rich_path_keeps_delimiters(self):
        got = _rich_plain(REPORTED)
        assert 'PickSpreadBlock(0)=="sculk_block"' in got
        assert '(69)=="sculk_block"' in got


# ══════════════════════════════════════════════════════════
# 2. 代码语义的定界符保留（词内运算符 / 字符串字面量）
# ══════════════════════════════════════════════════════════


class TestCodeSemanticsDelimiters:
    def test_all_snippets_preserved_inline(self):
        for src in CODE_SNIPPETS:
            assert _inline_plain(src) == src, src

    def test_all_snippets_preserved_block(self):
        for src in CODE_SNIPPETS:
            assert _plain(src) == src, src

    def test_no_style_leak(self):
        for src in CODE_SNIPPETS:
            assert _highlighted_texts(src) == [], src

    def test_word_internal_comparison_chain(self):
        src = "if a==b and c==d: pass"
        assert _plain(src) == src

    def test_string_literal_comparison_after_punctuation(self):
        src = 'PickSpreadBlock(0)=="sculk_block"'
        assert _plain(src) == src

    def test_paren_comparison_pair(self):
        src = "(0)==1, (1)==2"
        assert _plain(src) == src

    def test_operator_chain_with_brackets(self):
        src = "arr[0]==1, arr[1]==2"
        assert _plain(src) == src


class TestSiblingDelimitersCodeSemantics:
    """``~~``（删除线）/ ``||``（剧透）/ ``++``（下划线）的同类判否。"""

    def test_logical_or_chain_preserved(self):
        src = "a||b || c||d"
        assert _plain(src) == src
        assert _styled_texts(src, lambda s: s.fg == 240) == []

    def test_approx_chain_preserved(self):
        src = "a~~b, c~~d"
        assert _plain(src) == src
        assert _styled_texts(src, lambda s: s.dim) == []

    def test_increment_operators_preserved(self):
        src = "i++; j++ 与 i++ + j++"
        assert _plain(src) == src
        assert _styled_texts(src, lambda s: s.underline) == []


# ══════════════════════════════════════════════════════════
# 3. 正常语法不回归
# ══════════════════════════════════════════════════════════


class TestNormalMarkupStillWorks:
    def test_highlight_basic(self):
        assert _highlighted_texts("==hi==") == ["hi"]
        assert _plain("==hi==") == "hi"

    def test_highlight_cjk(self):
        assert _highlighted_texts("中文==高亮==中文") == ["高亮"]
        assert _plain("中文==高亮==中文") == "中文高亮中文"

    def test_highlight_between_spaces(self):
        assert _highlighted_texts("a ==b== c") == ["b"]

    def test_highlight_after_punctuation(self):
        assert _highlighted_texts("（==高亮==）") == ["高亮"]

    def test_highlight_line_end(self):
        assert _selected_highlight(_render("==行尾高亮==\n")) == ["行尾高亮"]

    def test_highlight_cross_soft_line_break(self):
        assert _highlighted_texts("==高亮\n跨行==") == ["高亮", "跨行"]
        assert _plain("==高亮\n跨行==") == "高亮\n跨行"

    def test_strikethrough_underline_spoiler(self):
        assert _styled_texts("~~删除线~~", lambda s: s.dim) == ["删除线"]
        assert _styled_texts("++下划线++", lambda s: s.underline) == ["下划线"]
        assert _styled_texts("||剧透||", lambda s: s.fg == 240) == ["██"]

    def test_spaced_delimiters_plain(self):
        assert _plain("a == b == c") == "a == b == c"
        assert _plain("x || y || z") == "x || y || z"
        assert _plain("x ~~ y ~~ z") == "x ~~ y ~~ z"

    def test_arrow_forms_unaffected(self):
        got = _plain("箭头 ==> 与 <== 与 <==>")
        assert "⟹" in got and "⟸" in got and "⟺" in got
        assert _highlighted_texts("箭头 ==> 与 <== 与 <==>") == []

    def test_rich_path_highlight_still_works(self):
        got = _rich_plain("中文==高亮==中文")
        assert "中文高亮中文" in got
        assert "==" not in got


def _selected_highlight(lines) -> list:
    return [r.text for ln in lines for r in ln.runs
            if r.style is not None and getattr(r.style, "bg", None) == _HL_BG]


# ══════════════════════════════════════════════════════════
# 4. 预览 / 提交一致性
# ══════════════════════════════════════════════════════════


class TestPreviewCommitConsistency:
    def test_preview_matches_commit_for_reported(self):
        r = AnsiStreamRenderer(width=100)
        r.write(REPORTED)
        preview = "\n".join(ln.plain for ln in r.take_preview_lines())
        assert 'PickSpreadBlock(0)"' not in preview
        r.close()
        committed = "\n".join(ln.plain for ln in r.take_lines())
        assert committed == _plain(REPORTED)

    def test_preview_matches_commit_for_code_snippet(self):
        src = 'PickSpreadBlock(0)=="sculk_block"; (69)=="sculk_block"\n'
        r = AnsiStreamRenderer(width=100)
        r.write(src)
        preview = "\n".join(ln.plain for ln in r.take_preview_lines())
        assert '"' not in preview.replace('"sculk_block"', '')
        r.close()
        committed = "\n".join(ln.plain for ln in r.take_lines())
        assert committed == _plain(src)

    def test_chunked_highlight_equivalence(self):
        src = "中文==高亮==中文 与 PickSpreadBlock(0)==\"sculk_block\""
        assert _chunked_plain(src, size=2) == _plain(src)


# ══════════════════════════════════════════════════════════
# 5. 嵌套上下文（引用 / 表格 / 列表 / 链接 / 强调 / 行内代码）
# ══════════════════════════════════════════════════════════


class TestNestedContexts:
    def test_blockquote_comparison(self):
        assert _plain("> a==b, c==d\n") == "│ a==b, c==d"

    def test_table_cell_comparison_and_highlight(self):
        src = "| a | b |\n| --- | --- |\n| x==y, z==w | ==hi== |\n"
        got = _plain(src)
        assert "x==y, z==w" in got
        assert "hi" in got
        assert _highlighted_texts(src) == ["hi"]

    def test_list_item_comparison(self):
        assert _plain("- a==b, c==d\n") == "• a==b, c==d"

    def test_link_text_comparison(self):
        assert _plain("链接 [a==b](http://x.y) 结束\n") == "链接 a==b 结束"

    def test_bold_wrapping_comparison(self):
        src = "**a==b and c==d**\n"
        assert _plain(src) == "a==b and c==d"
        assert _styled_texts(src, lambda s: s.bold) == ["a==b and c==d"]

    def test_inline_code_and_comparison(self):
        assert _plain("`a==b` and c==d\n") == "a==b and c==d"
