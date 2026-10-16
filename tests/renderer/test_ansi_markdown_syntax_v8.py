"""ANSI 流式 Markdown 语法/渲染增强（第八批）测试。

覆盖本轮改动：

  - 新增语法：``[TOC]`` 变体（大小写不敏感 + ``[[TOC]]`` 双括号）、fenced div
    Pandoc 属性 ``::: {.warning #id}``（类名作为容器类型）、HTML 表格
    ``colspan`` / ``rowspan``（跨行/跨列展开）；
  - 渲染错误修复：任务列表 checkbox 后无空白时**吞掉正文首字符**
    （``- [x]done`` → ``done`` 变 ``one``）、图片 alt 含嵌套方括号不解析
    （``![a [b]](u)``）、HTML 表格列数不符的数据行被整行丢弃；
  - 渲染错误修复（嵌套容器）：fenced div 嵌套时内层 ``:::` 被误当外层闭合
    （外层提前关闭、内层内容外泄、容器数量错乱）；
  - 性能：含行内格式的单行活动行流式预览（尾部窗口 + 帧成本节流）——
    22500 字符格式密集段落 30s → 亚秒级，且提交产出与一次性渲染一致。
"""

from __future__ import annotations

import io
import time

from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_chunked(src: str, size: int = 2, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _rich(src: str) -> str:
    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write(src)
    r.close()
    return buf.getvalue()


def _table_body(lines) -> list:
    """取框线表格的数据/表头行（``│`` 开头）。"""
    return [ln for ln in lines if ln.startswith("\u2502")]


# ══════════════════════════════════════════════════════════
# 新增语法 1：[TOC] 变体（大小写不敏感 + [[TOC]]）
# ══════════════════════════════════════════════════════════


def test_toc_upper_case_classic():
    got = _render("# a\n\n[TOC]\n")
    assert any("\u76ee\u5f55" in ln for ln in got)


def test_toc_lower_case():
    """``[toc]``（小写）同样渲染目录——修复前原样输出。"""
    got = _render("# a\n\n[toc]\n")
    assert any("\u76ee\u5f55" in ln for ln in got)
    assert not any(ln.strip() == "[toc]" for ln in got)


def test_toc_double_bracket():
    """``[[TOC]]`` 渲染目录——修复前被当 wikilink 显示为 ``TOC``。"""
    got = _render("# a\n\n[[TOC]]\n")
    assert any("\u76ee\u5f55" in ln for ln in got)
    assert not any(ln.strip() == "TOC" for ln in got)


def test_toc_mixed_case_double_bracket():
    got = _render("# a\n\n[[toc]]\n")
    assert any("\u76ee\u5f55" in ln for ln in got)


def test_toc_variant_in_blockquote():
    got = _render("> [toc]\n\n# A\n")
    assert any("\u76ee\u5f55" in ln for ln in got)


def test_toc_variant_chunked_consistent():
    for src in ("# a\n\n[toc]\n", "# a\n\n[[TOC]]\n"):
        assert _render_chunked(src) == _render(src)


def test_toc_variant_rich_path():
    out = _rich("[[TOC]]\n\n# A\n")
    assert "\u76ee\u5f55" in out


def test_forward_ref_regex_toc_variants():
    """TUI 前向引用重渲染正则同步支持 TOC 变体（否则关闭时不重渲染目录）。"""
    from src.tui.app.model import _FORWARD_REF_RE

    assert _FORWARD_REF_RE.search("[TOC]\n")
    assert _FORWARD_REF_RE.search("[toc]\n")
    assert _FORWARD_REF_RE.search("[[TOC]]\n")
    assert _FORWARD_REF_RE.search("> > [[toc]]\n")


# ══════════════════════════════════════════════════════════
# 新增语法 2：fenced div Pandoc 属性 ::: {.warning #id}
# ══════════════════════════════════════════════════════════


def test_fenced_div_pandoc_class():
    assert _render("::: {.warning}\nbody\n:::\n") == ["\u25aa WARNING", "  body"]


def test_fenced_div_pandoc_class_with_id():
    got = _render("::: {.tip #t1}\nbody\n:::\n")
    assert got[0].endswith("TIP")


def test_fenced_div_pandoc_class_with_title():
    got = _render("::: {.note} Title\nbody\n:::\n")
    assert "NOTE" in got[0] and "Title" in got[0]


def test_fenced_div_pandoc_no_class_falls_back():
    """``::: {#id}``（无类名）回落默认类型，不显示属性原文。"""
    got = _render("::: {#only}\nbody\n:::\n")
    assert got[0].endswith("NOTE")
    assert "{#only}" not in got[0]


def test_fenced_div_invalid_attrs_keeps_legacy():
    """``{color:red}`` 不是 Pandoc 属性 → 保持既有行为（整串作为类型）。"""
    got = _render("::: {color:red}\nbody\n:::\n")
    assert "{COLOR:RED}" in got[0]


def test_fenced_div_plain_type_unchanged():
    assert _render("::: warning\nbody\n:::\n") == ["\u25aa WARNING", "  body"]


def test_fenced_div_attrs_chunked_consistent():
    src = "::: {.warning #w}\nbody\n:::\n"
    assert _render_chunked(src) == _render(src)


def test_fenced_div_attrs_rich_path():
    out = _rich("::: {.warning}\nbody\n:::\n")
    assert "WARNING" in out and "{.warning}" not in out


# ══════════════════════════════════════════════════════════
# 修复 3：嵌套 fenced div
# ══════════════════════════════════════════════════════════


def test_fenced_div_nested_basic():
    got = _render("::: outer\n::: inner\ninside\n:::\nafter inner\n:::\n")
    assert got == ["\u25aa OUTER", "  \u25aa INNER", "    inside", "  after inner"]


def test_fenced_div_nested_deep():
    got = _render("::: a\n::: b\n::: c\nx\n:::\ny\n:::\nz\n:::\n")
    assert any("x" in ln for ln in got)
    assert not any("NOTE" in ln for ln in got)


def test_fenced_div_nested_trailing_not_extraneous_container():
    """嵌套闭合后不再产生多余的顶层容器（修复前末尾多出 ``▪ NOTE``）。"""
    got = _render("::: outer\n::: inner\ninside\n:::\n:::\n")
    assert got[0].endswith("OUTER")
    assert not any(ln.strip() == "\u25aa NOTE" for ln in got)


def test_fenced_div_nested_chunked_consistent():
    src = "::: outer\n::: inner\ninside\n:::\nafter\n:::\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 新增语法 4：HTML 表格 colspan / rowspan / 列宽容
# ══════════════════════════════════════════════════════════


def test_html_table_colspan_header_keeps_data():
    """``<th colspan="2">`` 表头 + 两列数据行——数据行不再整行丢失。"""
    lines = _render(
        '<table><tr><th colspan="2">h</th></tr>'
        '<tr><td>a</td><td>b</td></tr></table>\n'
    )
    body = _table_body(lines)
    assert any("a" in ln and "b" in ln for ln in body), lines


def test_html_table_rowspan_fills_covered_cell():
    lines = _render(
        '<table><tr><td rowspan="2">r</td><td>1</td></tr>'
        '<tr><td>2</td></tr></table>\n'
    )
    body = _table_body(lines)
    assert any("r" in ln and "1" in ln for ln in body), lines
    assert any("2" in ln for ln in body), lines


def test_html_table_extra_cells_kept():
    """首行 1 列、次行 2 列 → 列数自适应为 2，数据保留。"""
    lines = _render(
        '<table><tr><th>h</th></tr><tr><td>a</td><td>b</td></tr></table>\n'
    )
    assert any("a" in ln and "b" in ln for ln in _table_body(lines)), lines


def test_html_table_ragged_rows_render_table():
    lines = _render(
        '<table><tr><td>x</td><td>y</td><td>z</td></tr>'
        '<tr><td>1</td></tr></table>\n'
    )
    body = _table_body(lines)
    assert any("x" in ln for ln in body) and any("1" in ln for ln in body)


def test_html_table_colspan_chunked_consistent():
    src = ('<table><tr><th colspan="2">h</th></tr>'
           '<tr><td>a</td><td>b</td></tr></table>\n')
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 修复 5：任务列表 checkbox 后须为空白（不吞正文首字符）
# ══════════════════════════════════════════════════════════


def test_task_checkbox_requires_trailing_space():
    """``- [x]done`` 是普通列表项，文本原样保留（修复前吞掉 ``d``）。"""
    assert _render("- [x]done\n") == ["\u2022 [x]done"]


def test_task_checkbox_no_space_keeps_first_char():
    assert _render("- [x]abc\n") == ["\u2022 [x]abc"]
    assert _render("- [ ]abc\n") == ["\u2022 [ ]abc"]


def test_task_checkbox_with_space_still_works():
    assert _render("- [x] done\n") == ["\u2022 [x] done"]
    assert _render("- [ ] todo\n") == ["\u2022 [ ] todo"]


def test_task_checkbox_empty_item():
    got = _render("- [ ]\n")
    assert got and got[0].startswith("\u2022 [ ]")


def test_task_checkbox_parser_flags():
    from src.renderer._block_parser import RegexFreeBlockParser

    parse = RegexFreeBlockParser._parse_list_item_checkbox
    assert parse("[x]done")[1] is False
    assert parse("[x] done")[1:] == (True, True, False)
    assert parse("[ ]")[1] is True
    assert parse("[-]cancelled")[1] is False


def test_task_checkbox_rich_is_todo():
    from src.renderer._rendering import is_todo

    assert is_todo("[x]done")[0] is None
    assert is_todo("[x] done")[0] == "x"
    assert is_todo("[ ]")[0] == " "


def test_task_checkbox_rich_path_no_char_loss():
    out = _rich("- [x]done\n")
    assert "[x]done" in out and "one\\n" not in out


def test_task_checkbox_chunked_consistent():
    src = "- [x]done\n- [ ] ok\n"
    assert _render_chunked(src) == _render(src)


# ══════════════════════════════════════════════════════════
# 修复 6：图片 alt 嵌套方括号
# ══════════════════════════════════════════════════════════


def test_image_alt_nested_brackets():
    got = _render("![a [b]](u)\n")
    assert len(got) == 1 and "a [b]" in got[0] and "(u)" in got[0]


def test_image_alt_escaped_bracket():
    got = _render("![a \\] b](u)\n")
    assert got and "a \\] b" in got[0]


def test_image_alt_nested_chunked_consistent():
    src = "![a [b]](u)\n"
    assert _render_chunked(src) == _render(src)


def test_image_plain_still_works():
    got = _render("![alt](img.png)\n")
    assert len(got) == 1 and "alt" in got[0]


# ══════════════════════════════════════════════════════════
# 流式一致性（新增/修复项）
# ══════════════════════════════════════════════════════════


def test_new_syntax_stream_consistency_matrix():
    cases = [
        "# a\n\n[toc]\n",
        "# a\n\n[[TOC]]\n",
        "::: {.warning}\nbody\n:::\n",
        "::: outer\n::: inner\nx\n:::\ny\n:::\n",
        '<table><tr><th colspan="2">h</th></tr><tr><td>a</td><td>b</td></tr></table>\n',
        "- [x]done\n- [ ] keep\n",
        "![a [b]](u)\n",
    ]
    for src in cases:
        assert _render_chunked(src, 1) == _render(src), src
        assert _render_chunked(src, 3) == _render(src), src


# ══════════════════════════════════════════════════════════
# 性能：格式密集单行活动行流式预览
# ══════════════════════════════════════════════════════════

_FMT_SENTENCE = (
    "**bold** _italic_ `code` [link](http://x) ~~del~~ ==hi== "
    "a^2^ b~2~ :smile: "
)


def test_format_heavy_paragraph_stream_budget():
    """格式密集单行长段落流式写入不再 O(n²)（修复前 22500 字符约 30s）。"""
    text = _FMT_SENTENCE * 300
    t0 = time.perf_counter()
    r = AnsiStreamRenderer(width=100)
    for i in range(0, len(text), 8):
        r.write(text[i:i + 8])
    r.close()
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"\u683c\u5f0f\u5bc6\u96c6\u6bb5\u843d\u6d41\u5f0f\u8017\u65f6 {elapsed:.3f}s"


def test_format_heavy_committed_matches_oneshot():
    """提交产出与一次性渲染一致（节流只影响预览，不影响 committed 行）。"""
    text = _FMT_SENTENCE * 200 + "\n"
    assert _render_chunked(text, 8, width=100) == _render(text, width=100)


def test_format_active_line_throttle_reuses_rows():
    """同一段落追加（增量 < step）复用上一次渲染行（不重渲染）。"""
    r = AnsiStreamRenderer(width=100)
    base = _FMT_SENTENCE * 100
    r._note_paragraph_triggers(base)
    r._note_paragraph_triggers(base + " ")
    assert r._para_is_append is True
    rows1 = r._render_format_active_line(base + " ")
    rows2 = r._render_format_active_line(base + " x")
    assert rows2 is rows1


def test_format_active_line_no_reuse_across_paragraphs():
    """跨段落（非追加）不复用上一段落的渲染行。"""
    r = AnsiStreamRenderer(width=100)
    a = _FMT_SENTENCE * 60
    r._note_paragraph_triggers(a)
    r._render_format_active_line(a)
    b = "**另一段** `other` [x](http://y) ~~z~~ ==w== " * 90
    r._note_paragraph_triggers(b)
    assert r._para_is_append is False
    rows_b = r._render_format_active_line(b)
    plain = "".join(run.text for ln in rows_b for run in ln.runs)
    # 渲染的是新段落内容（窗口为 b 的尾部），而非复用 a 的行
    assert "另一段" in plain
    assert r._preview_throttle[("paragraph-format",)][0] == len(b)


def test_format_active_line_windows_tail():
    """超长活动行只渲染尾部窗口（成本封顶），尾部最新内容仍可见。"""
    from src.renderer.ansi import _FORMAT_ACTIVE_MAX_CHARS

    r = AnsiStreamRenderer(width=100)
    r._para_is_append = True
    text = "x" * 6000 + "**tail**"
    rows = r._render_format_active_line(text)
    plain = "".join(run.text for ln in rows for run in ln.runs)
    assert "tail" in plain
    assert len(plain) <= _FORMAT_ACTIVE_MAX_CHARS + 64


def test_format_active_line_short_line_full_render():
    """短格式行整行渲染（无窗口截断）。"""
    r = AnsiStreamRenderer(width=100)
    r._para_is_append = True
    text = "**bold** tail"
    rows = r._render_format_active_line(text)
    plain = "".join(run.text for ln in rows for run in ln.runs)
    assert plain == "bold tail"


def test_plain_paragraph_still_fast_path():
    """纯文本段落仍走单 Run 快路径（不受格式活动行节流影响）。"""
    r = AnsiStreamRenderer(width=100)
    text = "plain english sentence without any markdown markers " * 100
    r.write(text + "\n")
    r.close()
    lines = r.take_lines()
    assert lines and len(lines[0].runs) == 1


# ══════════════════════════════════════════════════════════
# 共享单元：Pandoc 属性解析（行内 span 与块级 fenced div 复用）
# ══════════════════════════════════════════════════════════


def test_pandoc_attrs_split_tokens_quotes():
    from src.renderer._pandoc_attrs import split_attr_tokens

    assert split_attr_tokens('.a #b title="x y"') == ['.a', '#b', 'title="x y"']


def test_pandoc_attrs_parse_basic():
    from src.renderer._pandoc_attrs import parse_pandoc_attrs

    attrs = parse_pandoc_attrs('.red #anc lang=python title="a b"')
    assert attrs == {
        "classes": ["red"],
        "id": "anc",
        "attrs": {"lang": "python", "title": "a b"},
    }


def test_pandoc_attrs_rejects_non_attr():
    from src.renderer._pandoc_attrs import parse_pandoc_attrs

    assert parse_pandoc_attrs("color:red") is None
    assert parse_pandoc_attrs("") is None
    assert parse_pandoc_attrs("  ") is None


def test_pandoc_attrs_braced_parse_and_consumed():
    from src.renderer._pandoc_attrs import parse_braced_attrs

    attrs, used = parse_braced_attrs("{.warning} Title")
    assert attrs is not None and used == len("{.warning}")
    assert parse_braced_attrs("{color:red}") == (None, 0)


def test_pandoc_attrs_type_from_attrs():
    from src.renderer._pandoc_attrs import pandoc_type_from_attrs

    assert pandoc_type_from_attrs({"classes": ["warning"]}) == "WARNING"
    assert pandoc_type_from_attrs({"classes": []}) == ""
    assert pandoc_type_from_attrs(None) == ""


def test_inline_span_still_works_after_refactor():
    """行内属性 span 复用共享解析后行为不变。"""
    assert _render("[x]{.red #i key=v}\n") == ["x"]


def test_inline_color_brace_not_treated_as_span():
    """``{color:red}`` 仍走既有花括号着色语义（非 Pandoc 属性）。"""
    r = AnsiStreamRenderer(width=72)
    r.write("{color:red}x{color}\n")
    r.close()
    runs = [run for ln in r.take_lines() for run in ln.runs if run.text == "x"]
    assert runs and runs[0].style is not None and runs[0].style.fg == 196
