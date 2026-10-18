"""ANSI 流式 Markdown 渲染修复（第二十一批）测试。

用户报告「流式 markdown 下面的东西渲染错误」，并贴出以下推理内容（感叹号
处为问题点）：

    … 可以通过 threadCount()==2（池有 2 线程）+ inFlightTasks 曾经 >1? …
    … 通常 >= 1。断言 >=1 稳妥，>=2 可能偶发失败。
    用 ">=1 且 threadCount==2"。

其中 ``==`` 是**比较运算符**（``threadCount()==2``、``threadCount==2``），
但修复前：

  - ``threadCount()==2`` 的 ``==`` 左邻是 ``)``（标点）、右邻是 ``2``
    （ASCII 词字符）——CommonMark flanking 判为合法左定界符，且
    ``_span_delim_code_open`` 只判「左邻词字符 / 右邻引号」，未覆盖
    「左操作数在右括号处结束」的形态；
  - 段落末尾普通文本里的词内比较 ``…threadCount==2`` 右邻是 ``2``、左邻是
    ``t``（right-flanking 成立）→ 被用作**闭合符**；

  两个 ``==`` 因此被吞掉，中间整段（含 ``threadCount() == 2`` 等行内代码）
  被误加高亮背景（``bg=11`` + 粗体）。流式预览与提交渲染同时出现，用户看到
  的正是「``==`` 消失 + 大半段变色」。

修复：``_span_delim_code_open`` 追加「左操作数在 ASCII 操作数结尾处结束
（``)`` / ``]`` / ``}`` / 引号 / 行内代码反引号）且右侧是 ASCII 操作数起点
（标识符字符 / 开括号）」判否。``==hi==`` / ``中文==高亮==中文`` /
``a ==b== c`` / ``（==高亮==）`` / ``==a==b`` / 跨软换行高亮等正常语法不变。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline

#: 高亮样式背景色（``ansi.inline._STYLE_HIGHLIGHT`` 的 bg）
_HL_BG = 11

#: 用户报告的内容（原样，第一段以软换行连接第二段）
REPORTED = (
    "多线程证明：在 4 个不同区块各放一个源水或沙。创建 chunks (0,0),(1,0),(0,1),(1,1)。"
    "每个区块地面 y=63 + 源水。OnBlockChanged 4 处 → pending 4 个位置 → 一个批次 4 个任务"
    "（4 区块）→ 多线程并行。经过多次 tick，水扩散。检查 completedTasks >= 4 且 "
    "appliedBatches>0。但单线程也会完成 → 无法严格证明并行。可以通过 "
    "threadCount()==2（池有 2 线程）+ inFlightTasks 曾经 >1? 不太可靠。\n"
    "可以验证 `threadCount() == 2` 且 `completedTasks() >= 4`，说明任务由 2 个后台线程池执行"
    "（计算在后台）。这已满足\"多线程完成更新\"的实现证据。也可以在测试中放置大量更新使其同时提交"
    "多个任务，然后 `RunSync` 期间检查 inFlightTasks 曾 >= 2？RunSync 内部等待；可以在 "
    "SubmitBatch 后立即检查 `engine.inFlightTasks()`。但 SubmitBatch 是私有。可以 Tick 后调用 "
    "engine.Update()（会提交并立即返回），然后检查 `engine.batchInFlight()` 和 "
    "`engine.inFlightTasks() >= 2`。但任务可能瞬间完成。用一个更大的批次（>100 位置）和 4 个区块，"
    "`inFlightTasks()` 提交后立即读取，通常 >= 1。断言 >=1 稳妥，>=2 可能偶发失败。"
    "用 \">=1 且 threadCount==2\""
)

#: 修复前被吞后出现的形态
_EATEN_MARKERS = (
    "threadCount()2",
    "threadCount2\"",
)


def _render(src: str, width: int = 106):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return r.take_lines()


def _plain(src: str, width: int = 106) -> str:
    return "\n".join(ln.plain for ln in _render(src, width))


def _inline_plain(src: str) -> str:
    return "".join(r.text for r in render_inline(src))


def _highlighted_texts(src: str, width: int = 106) -> list[str]:
    out: list[str] = []
    for ln in _render(src, width):
        for run in ln.runs:
            style = run.style
            if style is not None and getattr(style, "bg", None) == _HL_BG:
                out.append(run.text)
    return out


def _chunked_plain(src: str, size: int = 3, width: int = 106) -> str:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return "\n".join(ln.plain for ln in r.take_lines())


# ══════════════════════════════════════════════════════════
# 1. 用户报告内容（本次修复核心）
# ══════════════════════════════════════════════════════════


class TestReportedDoubleEqualsOperators:
    def test_committed_keeps_operator_delimiters(self):
        got = _plain(REPORTED)
        assert "threadCount()==2" in got
        assert "threadCount==2" in got
        assert got.count("==") == REPORTED.count("==")

    def test_committed_no_eaten_marker(self):
        got = _plain(REPORTED)
        for bad in _EATEN_MARKERS:
            assert bad not in got, (bad, got)

    def test_committed_no_highlight_leak(self):
        assert _highlighted_texts(REPORTED) == []

    def test_inline_code_spans_keep_style(self):
        """行内代码仍按代码样式渲染（不被误高亮吞并）。"""
        lines = _render(REPORTED)
        bold_code = [
            run.text
            for ln in lines
            for run in ln.runs
            if getattr(run.style, "fg", None) == 46
        ]
        assert "threadCount() == 2" in bold_code
        assert "completedTasks() >= 4" in bold_code

    def test_chunked_equivalence(self):
        base = _plain(REPORTED)
        for size in (1, 2, 3, 5, 8, 17):
            assert _chunked_plain(REPORTED, size=size) == base, size

    def test_streaming_frames_never_eat_delimiters(self):
        """流式任意一帧的已提交行 / 预览都不得出现被吞形态或高亮泄漏。"""
        r = AnsiStreamRenderer(width=106)
        for i in range(0, len(REPORTED), 3):
            r.write(REPORTED[i:i + 3])
            frames = (r.lines, r.take_preview_lines())
            for lines in frames:
                text = "\n".join(ln.plain for ln in lines)
                for bad in _EATEN_MARKERS:
                    assert bad not in text, (i, bad, text)
                for ln in lines:
                    for run in ln.runs:
                        assert getattr(run.style, "bg", None) != _HL_BG, (
                            i, run.text, text)
        r.close()


# ══════════════════════════════════════════════════════════
# 2. 「左操作数在括号 / 引号 / 代码处结束」的运算符判否
# ══════════════════════════════════════════════════════════


class TestOperandEndDelimiters:
    def test_call_result_comparison_preserved(self):
        src = "threadCount()==2（池有 2 线程）+ 断言 threadCount==2"
        assert _plain(src) == src
        assert _highlighted_texts(src) == []

    def test_paren_operands_chain_preserved(self):
        src = "if (a)==(b) and (d)==(e): pass"
        assert _plain(src) == src
        assert _highlighted_texts(src) == []

    def test_call_comparison_then_word_comparison(self):
        src = "f()==2，且 h==i"
        assert _plain(src) == src
        assert _highlighted_texts(src) == []

    def test_index_operand_then_word_comparison(self):
        src = "arr[0]==(x) 然后 a==b"
        assert _plain(src) == src
        assert _highlighted_texts(src) == []

    def test_string_literal_operand_preserved(self):
        src = '"a"==2 与 x==y'
        assert _plain(src) == src
        assert _highlighted_texts(src) == []

    def test_inline_code_operand_preserved(self):
        # 行内代码闭合反引号后紧跟 ``==``：比较运算符（反引号渲染时不可见）
        src = "`x`==y 与 a==b"
        assert _plain(src) == "x==y 与 a==b"
        assert _highlighted_texts(src) == []

    def test_inline_api_matches(self):
        for src in (
            "threadCount()==2 与 threadCount==2",
            "f()==2，且 h==i",
            '"a"==2 与 x==y',
        ):
            assert _inline_plain(src) == src

    def test_sibling_delimiters_paren_operands(self):
        assert _plain("(a)||(b)") == "(a)||(b)"
        assert _plain("arr[0]++x") == "arr[0]++x"


# ══════════════════════════════════════════════════════════
# 3. 正常语法不回归
# ══════════════════════════════════════════════════════════


class TestNormalHighlightStillWorks:
    def test_basic(self):
        assert _plain("==hi==") == "hi"
        assert _highlighted_texts("==hi==") == ["hi"]

    def test_cjk(self):
        assert _plain("中文==高亮==中文") == "中文高亮中文"
        assert _highlighted_texts("中文==高亮==中文") == ["高亮"]

    def test_after_punctuation(self):
        assert _plain("（==高亮==）") == "（高亮）"
        assert _highlighted_texts("（==高亮==）") == ["高亮"]

    def test_ascii_paren_around_highlight(self):
        assert _plain("(==hi==)") == "(hi)"
        assert _highlighted_texts("(==hi==)") == ["hi"]

    def test_between_spaces(self):
        assert _plain("a ==b== c") == "a b c"
        assert _highlighted_texts("a ==b== c") == ["b"]

    def test_opener_at_line_start(self):
        assert _plain("==a==b") == "ab"
        assert _highlighted_texts("==a==b") == ["a"]

    def test_cross_soft_line_break(self):
        assert _plain("==高亮\n跨行==") == "高亮\n跨行"
        # 内容按软换行拆分为两个带高亮背景的 run
        assert _highlighted_texts("==高亮\n跨行==") == ["高亮", "跨行"]

    def test_spaced_delimiters_stay_literal(self):
        assert _plain("a == b == c") == "a == b == c"
        assert _highlighted_texts("a == b == c") == []

    def test_word_internal_comparison_stays_literal(self):
        for src in ("x==y", "A==B==C", 'mode=="change"', "f(x)==y; g(z)==w"):
            assert _plain(src) == src
            assert _highlighted_texts(src) == []
