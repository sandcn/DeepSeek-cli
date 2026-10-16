"""HTML 块级解析辅助模块（``renderer._block_html``）守卫与行为测试。

该模块从 ``renderer/_block_parser.py`` 抽出（后者曾达 4500+ 行）。本测试：

1. 固化模块与块级解析器 re-export 的是**同一对象**（旧导入路径兼容）；
2. 覆盖字符级扫描的正确性（自闭合、未闭合、标签名边界、嵌套、编号）；
3. 覆盖 ``colspan`` / ``rowspan`` 布局与列数自适应（列数不符的行不丢弃）。
"""

from __future__ import annotations

import pytest

from src.renderer import _block_html as bh
from src.renderer import _block_parser as bp


# ── re-export 一致性 ──────────────────────────────────────


@pytest.mark.parametrize("name", sorted(bh.__all__))
def test_block_parser_reexports_same_objects(name):
    assert getattr(bp, name) is getattr(bh, name)


def test_block_parser_does_not_define_html_helpers_itself():
    """实现应位于 _block_html（防止回涨）。"""
    import inspect

    for name in bh.__all__:
        if name.startswith("_"):
            obj = getattr(bh, name)
            if callable(obj):
                module = getattr(inspect.getmodule(obj), "__name__", "")
                assert module == "src.renderer._block_html", f"{name} 定义在 {module}"


# ── _iter_html_tag_blocks ─────────────────────────────────


def test_iter_tag_blocks_simple():
    assert list(bh._iter_html_tag_blocks("<tr>a</tr><tr>b</tr>", "tr")) == ["a", "b"]


def test_iter_tag_blocks_respects_name_boundary():
    """``<tr`` 不应匹配 ``<track``。"""
    assert list(bh._iter_html_tag_blocks("<track src='x'><tr>a</tr>", "tr")) == ["a"]


def test_iter_tag_blocks_self_closing_yields_empty():
    assert list(bh._iter_html_tag_blocks("<tr/>text", "tr")) == [""]


def test_iter_tag_blocks_unclosed_takes_remainder():
    assert list(bh._iter_html_tag_blocks("x<tr>tail", "tr")) == ["tail"]


def test_iter_tag_blocks_case_insensitive():
    assert list(bh._iter_html_tag_blocks("<TR>a</TR>", "tr")) == ["a"]


# ── 单元格提取 ────────────────────────────────────────────


def test_extract_html_cells_mixed_th_td():
    assert bh._extract_html_cells("<th>A</th><td>B</td>") == ["A", "B"]


def test_iter_html_cells_keeps_attrs_text():
    cells = list(bh._iter_html_cells('<td colspan="2">A</td>'))
    assert cells == [("A", '<td colspan="2">')]


def test_extract_html_cell_aligns_reads_align_attribute():
    aligns = bh._extract_html_cell_aligns('<th align="center">A</th><td>B</td>')
    assert aligns[0] == "center"
    assert len(aligns) == 2


def test_extract_html_cells_unclosed_cell_takes_remainder():
    assert bh._extract_html_cells("<td>tail") == ["tail"]


# ── 跨列/跨行 ─────────────────────────────────────────────


def test_html_span_attr_defaults_and_clamp():
    assert bh._html_span_attr('<td colspan="3">', "colspan") == 3
    assert bh._html_span_attr("<td>", "colspan") == 1
    assert bh._html_span_attr('<td colspan="abc">', "colspan") == 1
    assert bh._html_span_attr('<td colspan="999">', "colspan") == 50
    assert bh._html_span_attr('<td colspan="0">', "colspan") == 1


def test_layout_html_table_rows_expands_colspan():
    rows = [["Title", "B"], ["x", "y"]]
    attrs = [['<th colspan="2">', "<th>"], ["<td>", "<td>"]]
    grid, ncols = bh._layout_html_table_rows(rows, attrs)
    # 首行 colspan=2 的单元格占两列 + 第二个 th → 共 3 列
    assert ncols == 3
    assert grid[0] == ["Title", "", "B"]
    assert grid[1] == ["x", "y", ""]


def test_layout_html_table_rows_expands_rowspan():
    rows = [["A", "B"], ["C"]]
    attrs = [['<td rowspan="2">', "<td>"], ["<td>"]]
    grid, ncols = bh._layout_html_table_rows(rows, attrs)
    assert ncols == 2
    assert grid[0] == ["A", "B"]
    assert grid[1][0] == ""      # 被上方 rowspan 覆盖
    assert grid[1][1] == "C"


def test_layout_html_table_rows_empty_inputs():
    assert bh._layout_html_table_rows([], []) == ([], 0)


def test_layout_html_table_rows_without_attrs():
    grid, ncols = bh._layout_html_table_rows([["a", "b"]], [])
    assert ncols == 2
    assert grid == [["a", "b"]]


# ── <pre> / <code> ────────────────────────────────────────


def test_html_pre_language_from_class():
    assert bh._html_pre_language('<pre class="language-python">') == "python"


def test_html_pre_language_from_nested_code():
    assert bh._html_pre_language('<pre><code class="lang-js">') == "js"


def test_html_pre_language_none():
    assert bh._html_pre_language("<pre>") == ""


def test_strip_inline_code_tags_keeps_other_inline_tags():
    assert bh._strip_inline_code_tags("<code>x = <b>1</b></code>") == "x = <b>1</b>"


def test_strip_inline_code_tags_ignores_name_boundary():
    assert bh._strip_inline_code_tags("<codexyz>") == "<codexyz>"


def test_strip_inline_code_tags_no_tags():
    assert bh._strip_inline_code_tags("plain") == "plain"


# ── 单行标签文本 ──────────────────────────────────────────


def test_after_open_tag_text():
    assert bh._after_open_tag_text('<img src="x.png"> 说明') == "说明"
    assert bh._after_open_tag_text("<img>") == ""
    assert bh._after_open_tag_text("plain") == ""
    assert bh._after_open_tag_text("<img") == ""


def test_html_int_attr():
    assert bh._html_int_attr('ol start="3"', "start") == 3
    assert bh._html_int_attr('li value="5"', "value") == 5
    assert bh._html_int_attr("li", "value") is None
    assert bh._html_int_attr('li value="abc"', "value") is None
    assert bh._html_int_attr("", "value") is None


def test_extract_html_tag_text():
    assert bh._extract_html_tag_text("<caption> 标题 </caption>", "caption") == "标题"
    assert bh._extract_html_tag_text("<p>x</p>", "caption") is None


# ── <dl> ──────────────────────────────────────────────────


def test_parse_html_dl_items_ordered():
    items = bh._parse_html_dl_items("<dt>Term</dt><dd>Def</dd>")
    assert items == [("dt", "Term"), ("dd", "Def")]


def test_parse_html_dl_items_unclosed_takes_remainder():
    assert bh._parse_html_dl_items("<dt>tail") == [("dt", "tail")]


def test_parse_html_dl_items_empty():
    assert bh._parse_html_dl_items("") == []


# ── <ul>/<ol> ─────────────────────────────────────────────


def test_parse_html_list_items_flat_unordered():
    items = bh._parse_html_list_items("<ul><li>a<li>b</ul>")
    assert [(d, ordered, num, text) for d, ordered, num, text in items] == [
        (1, False, 1, "a"), (1, False, 1, "b"),
    ]


def test_parse_html_list_items_ordered_numbering():
    items = bh._parse_html_list_items("<ol><li>a<li>b</ol>", root_kind="ol")
    assert [num for _d, _o, num, _t in items] == [1, 2]


def test_parse_html_list_items_ordered_start():
    items = bh._parse_html_list_items('<ol start="3"><li>a<li>b</ol>',
                                      root_kind="ol", start=3)
    assert [num for _d, _o, num, _t in items] == [3, 4]


def test_parse_html_list_items_nested_depth():
    items = bh._parse_html_list_items("<ul><li>a<ul><li>b</ul><li>c</ul>")
    depths = [(d, text) for d, _o, _n, text in items]
    assert depths[0] == (1, "a")
    assert depths[1] == (2, "b")
    assert depths[2] == (1, "c")


def test_parse_html_list_items_li_value_override():
    items = bh._parse_html_list_items('<ol><li value="7">a<li>b</ol>', root_kind="ol")
    assert [num for _d, _o, num, _t in items] == [7, 8]


def test_parse_html_list_items_keeps_inline_tags():
    items = bh._parse_html_list_items("<ul><li><b>bold</b> tail</ul>")
    assert items[0][3] == "<b>bold</b> tail"


def test_parse_html_list_items_bare_li_fallback():
    items = bh._parse_html_list_items("<li>only</li>")
    assert items == [(1, False, 1, "only")]


def test_parse_html_list_items_collapses_whitespace():
    items = bh._parse_html_list_items("<ul><li>a\n   b</ul>")
    assert items[0][3] == "a b"


def test_html_list_struct_tags_constant():
    assert bh._HTML_LIST_STRUCT_TAGS == frozenset({"ul", "ol", "li", "dl", "dt", "dd"})
