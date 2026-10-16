"""ANSI 流式 Markdown 语法/渲染增强（第十批）测试。

本轮改动（对齐 CommonMark / GFM 语义，TUI 与 Rich 两路径共享解析层）：

  - **语法语义补全**：

    * **强调定界符 run 语义**——flanking 判定基于**完整 run**（而非调用方
      尝试消耗的截断长度）；「run 尾部为空白」的定界符不再被误判为可开。
    * **嵌套强调**——``*(*foo*)*`` / ``_foo _bar_ baz_`` 等内外层定界符
      正确配对（此前内层定界符被当文本、标记残留）。
    * **行内代码 span 的 CommonMark 语义**——跨行内容换行规范化为空格；
      首尾同为空格时各剥离一个；反引号串无恰好匹配的闭合时整段为字面文本
      （不再从第二个反引号起重新配对）。
    * **围栏 info string**——反引号围栏的 info string 含反引号时整行不是
      围栏（CommonMark）。

  - **渲染错误修复**：

    * ``foo *****`` / ``**** is not…`` 等定界符标记不再被剥离（此前
      ``foo *****`` → ``foo *``，字符丢失）。
    * 行内代码跨软换行时，行尾双空格不再被误当硬换行（此前产出字面
      ``<br>`` 并污染代码内容）。
    * ``<foo\\+@bar.example.com>`` 的转义局部部分不再被误判为裸邮箱
      （此前渲染出重复的 ``+``）。
    * 反引号围栏 info 含反引号时不再被误判为围栏。

  - **性能**：强调解析新增**失败缓存**（``(kind, pos)``）——连续定界符 run
    的病态输入（``**********a**********a`` 等）此前指数级重复解析（2 组 run
    实测 0.62s、3 组超时），现按位置去重；``_delim_flanking`` 的完整 run
    判定走「``length`` 处已是 run 之外」快速路径（常见形态 O(1)）。

  - **保留项**：ASCII dunder 标识符（``__init__`` / ``__my_var__``）的
    「dunder 保护」（用户选定折中）沿用未改——``__bold__`` 这类纯 ASCII
    下划线写法仍按标识符原样输出；含非 ASCII（``__粗体__``）照常渲染粗体。

两路径同步：TUI（``AnsiStreamRenderer``）与 Rich（``IncrementalRenderer``）
共享同一解析层（``RecursiveDescentParser`` / ``_InlineParser``），语义一致。
"""

from __future__ import annotations

import re
import time

from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_chunked(src: str, size: int = 1, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def _rich_plain(src: str) -> list:
    import io

    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write(src)
    r.close()
    return [_ANSI.sub("", ln).rstrip() for ln in buf.getvalue().splitlines()]


# ══════════════════════════════════════════════════════════
# 语法语义补全 1：完整 run 的 flanking 判定
# ══════════════════════════════════════════════════════════


def test_trailing_star_run_kept():
    """``foo *****``（run 后为行尾空白）不构成强调，标记原样保留。"""
    assert _render("foo *****\n") == ["foo *****"]


def test_trailing_star_run_inline_kept():
    assert _render("foo ***** bar\n") == ["foo ***** bar"]


def test_trailing_underscore_run_kept():
    assert _render("foo _____\n") == ["foo _____"]


def test_star_run_before_text_kept():
    assert _render("**** is not an empty strong emphasis\n") == [
        "**** is not an empty strong emphasis"]


def test_underscore_run_before_text_kept():
    assert _render("____ is not an empty strong emphasis\n") == [
        "____ is not an empty strong emphasis"]


def test_bold_italic_normal():
    assert _render("***foo***\n") == ["foo"]
    assert _render("**foo** and *bar*\n") == ["foo and bar"]


# ══════════════════════════════════════════════════════════
# 语法语义补全 2：嵌套强调
# ══════════════════════════════════════════════════════════


def test_emphasis_nested_parens():
    assert _render("*(*foo*)*\n") == ["(foo)"]


def test_emphasis_nested_underscore_parens():
    assert _render("_(_foo_)_\n") == ["(foo)"]


def test_emphasis_nested_same_type():
    assert _render("_foo _bar_ baz_\n") == ["foo bar baz"]


def test_strong_inside_em_asterisk():
    assert _render("*foo **bar** baz*\n") == ["foo bar baz"]


def test_nested_emphasis_chunked_consistent():
    for src in ("*(*foo*)*\n", "_foo _bar_ baz_\n", "*foo **bar** baz*\n"):
        assert _render_chunked(src, 1) == _render(src)
        assert _render_chunked(src, 5) == _render(src)


# ══════════════════════════════════════════════════════════
# 语法语义补全 3：行内代码 span
# ══════════════════════════════════════════════════════════


def test_code_span_multiline_to_space():
    """代码 span 内换行规范化为空格（CommonMark）。"""
    assert _render("`foo\nbar`\n") == ["foo bar"]


def test_code_span_multiline_trailing_spaces():
    assert _render("`foo  \nbar`\n") == ["foo   bar"]


def test_code_span_no_hard_break_injected():
    """代码 span 内的行尾双空格不构成硬换行（不产出字面 ``<br>``）。"""
    out = _render("`foo  \nbar`\n")
    assert "<br>" not in "\n".join(out)


def test_code_span_strip_surrounding_space():
    assert _render("`` ` ``\n") == ["`"]
    assert _render("``` a ```\n") == ["a"]


def test_code_span_leading_space_only_kept():
    assert _render("` a`\n") == [" a"]


def test_code_span_unmatched_run_literal():
    """反引号串无恰好匹配的闭合时整段为字面文本。"""
    assert _render("```py foo``\n") == ["```py foo``"]


# ══════════════════════════════════════════════════════════
# 语法语义补全 4：围栏 info string
# ══════════════════════════════════════════════════════════


def test_fence_info_with_backtick_not_fence():
    """反引号围栏的 info string 含反引号 → 整行不是围栏（CommonMark）。"""
    out = _render("```py foo``\n")
    assert out == ["```py foo``"], out


def test_tilde_fence_info_may_contain_backtick():
    out = _render("~~~ info with ` ticks\nx\n~~~\n")
    joined = "\n".join(out)
    assert "x" in joined


def test_normal_fence_still_works():
    out = _render("```py\nx = 1\n```\n")
    joined = "\n".join(out)
    assert "x = 1" in joined
    assert "```" in joined


# ══════════════════════════════════════════════════════════
# 渲染错误修复：硬换行 / 转义
# ══════════════════════════════════════════════════════════


def test_hard_break_outside_code():
    assert _render("line1  \nline2\n") == ["line1", "line2"]


def test_backslash_hard_break_outside_code():
    assert _render("line1\\\nline2\n") == ["line1", "line2"]


def test_escaped_plus_email_not_autolink():
    assert _render("<foo\\+@bar.example.com>\n") == ["<foo+@bar.example.com>"]


def test_escaped_plus_email_inline():
    assert _render("mail me at a\\+b@x.com\n") == ["mail me at a+b@x.com"]


def test_plus_email_still_autolink():
    assert _render("x+tag@example.com\n") == ["x+tag@example.com"]


# ══════════════════════════════════════════════════════════
# 保留项：dunder 保护（用户选定折中，未改）
# ══════════════════════════════════════════════════════════


def test_dunder_protection_kept():
    assert _render("__init__ 说明\n") == ["__init__ 说明"]
    assert _render("调用 __my_var__ 方法\n") == ["调用 __my_var__ 方法"]


def test_cjk_underscore_bold_still_renders():
    assert _render("__粗体__ 结束\n") == ["粗体 结束"]


def test_intraword_underscore_not_emphasis():
    assert _render("foo_bar_baz\n") == ["foo_bar_baz"]
    assert _render("snake_case_name\n") == ["snake_case_name"]


# ══════════════════════════════════════════════════════════
# Rich 路径同步
# ══════════════════════════════════════════════════════════


def test_rich_path_code_multiline():
    out = "\n".join(_rich_plain("`foo\nbar`\n"))
    assert "foo bar" in out, out


def test_rich_path_nested_emphasis():
    out = "\n".join(_rich_plain("*(*foo*)*\n"))
    assert "(foo)" in out, out


def test_rich_path_star_run_kept():
    out = "\n".join(_rich_plain("foo *****\n"))
    assert "foo *****" in out, out


# ══════════════════════════════════════════════════════════
# 性能：病态定界符不指数爆炸
# ══════════════════════════════════════════════════════════


def test_pathological_star_runs_bounded():
    """连续定界符 run 的解析按位置去重（此前指数级重复）。"""
    src = ("*" * 10 + "a") * 5 + "\n"
    t0 = time.perf_counter()
    _render(src)
    assert time.perf_counter() - t0 < 1.0


def test_pathological_underscore_runs_bounded():
    src = ("_" * 10 + "a") * 5 + "\n"
    t0 = time.perf_counter()
    _render(src)
    assert time.perf_counter() - t0 < 1.0


def test_normal_emphasis_not_slowed():
    """常规强调文本仍在预算内（缓存不引入额外开销）。"""
    src = "*a* **b** _c_ __d__ " * 200 + "\n"
    t0 = time.perf_counter()
    _render(src)
    assert time.perf_counter() - t0 < 1.0


# ══════════════════════════════════════════════════════════
# 流式一致性
# ══════════════════════════════════════════════════════════


def test_stream_consistency_matrix():
    cases = [
        "_bar_ and **baz** and *qux*\n",
        "foo ***** bar\n",
        "`a\nb` and plain\n",
        "*(*foo*)* after\n",
        "line1  \nline2\n",
        "<foo\\+@bar.example.com>\n",
        "```py foo``\n",
        "__init__ 说明\n",
    ]
    for src in cases:
        whole = _render(src)
        for size in (1, 2, 5, 13):
            assert _render_chunked(src, size) == whole, (src, size)
