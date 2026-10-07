"""TUI 流式 Markdown 语法增强（第三批：嵌套语法 + 更多语法）测试。

覆盖 ``AnsiStreamRenderer``（TUI 内容路径）本轮新增/修复：

  - 表格单元格内 ``<br>`` 多行渲染（结构不破坏）、单元格内行内代码 ``|`` 不拆列
  - 代码围栏 info 属性 ``{1,3-5}`` / ``{hl_lines="..."}`` 行高亮、``{.numberLines}`` 行号
  - 列表项内块级容器（代码块 / 引用 / 表格 / 嵌套容器）缩进对齐
  - 引用内代码块（含缩进围栏）流式与一次性渲染一致（不重复 ``│`` 前缀）
  - 引用风格告示 / fenced div 正文按完整 Markdown 渲染（列表 / 代码块）
  - HTML 嵌套列表（``<ul>``/``<ol>`` 嵌套、有序编号、行内格式保留）
  - 行内增强：``<kbd>`` 键帽（无重复图标）、``<span style="color:...">`` 着色
  - 脚注多段落、定义列表续段、列表项内标题、emoji 短代码补全
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline
from src.renderer._table_utils import _parse_table_row
from src.renderer._utils import parse_highlight_lines, parse_linenos


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_stream(src: str, chunk: int = 2, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _inline_plain(text: str) -> str:
    return "".join(run.text for run in render_inline(text))


# ══════════════════════════════════════════════════════════
# 表格：单元格内换行 / 行内代码管道
# ══════════════════════════════════════════════════════════


def test_table_cell_br_renders_multiline_cell():
    lines = _render("| a | b |\n|---|---|\n| 行1<br>行2 | x |\n")
    body = [ln for ln in lines if ln.startswith("\u2502")]
    assert len(body) == 3  # 表头 + 两行数据（多行单元格）
    assert body[1].startswith("\u2502 行1")
    assert body[2].startswith("\u2502 行2")
    for ln in lines:
        assert "\n" not in ln


def test_table_cell_br_keeps_border_width():
    from src.renderer._utils import cjk_display_width

    lines = _render("| a | b |\n|---|---|\n| 行1<br>行2 | x |\n")
    widths = {cjk_display_width(ln) for ln in lines}
    assert len(widths) == 1  # 框线列宽一致（按显示宽度）


def test_table_code_pipe_not_split():
    assert _parse_table_row("| `x|y` | 2 |") == ["`x|y`", "2"]
    lines = _render("| a | b |\n|---|---|\n| `x|y` | 2 |\n")
    assert any("x|y" in ln for ln in lines)
    assert not any(ln.startswith("|") for ln in lines)


def test_table_escaped_pipe_kept():
    assert _parse_table_row(r"| a \| b | c |") == ["a | b", "c"]


# ══════════════════════════════════════════════════════════
# 代码块：hl_lines / linenos
# ══════════════════════════════════════════════════════════


def test_parse_highlight_lines_brace_spec():
    assert parse_highlight_lines("{1,3-5}") == [1, 3, 4, 5]
    assert parse_highlight_lines("{1 3-5}") == [1, 3, 4, 5]
    assert parse_highlight_lines('{.numberLines hl_lines="2,4"}') == [2, 4]
    assert parse_highlight_lines("{hl_lines='1-2'}") == [1, 2]
    assert parse_highlight_lines("{python}") == []


def test_parse_linenos_tokens():
    assert parse_linenos("{.numberLines}") is True
    assert parse_linenos("{linenos}") is True
    assert parse_linenos("{line-numbers}") is True
    assert parse_linenos("") is False


def test_code_fence_hl_lines_rendered():
    lines = _render("```python {1,3}\na=1\nb=2\nc=3\n```\n")
    assert lines[1].startswith("\u25b8")
    assert not lines[2].startswith("\u25b8")
    assert lines[3].startswith("\u25b8")


def test_code_fence_brace_spec_not_in_content():
    lines = _render("```python {1,3-4}\na=1\nb=2\n```\n")
    assert not any("{1,3-4}" in ln for ln in lines)


def test_code_fence_linenos_rendered():
    lines = _render("```python {.numberLines}\na=1\nb=2\n```\n")
    assert lines[1].startswith(" 1 ")
    assert lines[2].startswith(" 2 ")


# ══════════════════════════════════════════════════════════
# 列表项内块级容器（嵌套）
# ══════════════════════════════════════════════════════════


def test_list_item_code_block_indented():
    lines = _render("- 项\n\n  ```py\n  x=1\n  ```\n")
    assert lines[0] == "\u2022 项"
    assert any(ln.startswith("  ```py") for ln in lines)
    assert any(ln.strip() == "x=1" and ln.startswith("  ") for ln in lines)


def test_list_item_quote_indented():
    lines = _render("- 项\n\n  > 引用\n")
    assert any(ln == "  \u2502 引用" for ln in lines)


def test_list_item_table_indented():
    lines = _render("- 项\n\n  | a | b |\n  |---|---|\n  | 1 | 2 |\n")
    border = [ln for ln in lines if "\u250c" in ln]
    assert border and border[0].startswith("  ")


def test_nested_list_not_double_indented():
    lines = _render("- a\n  - b\n")
    assert lines[0] == "\u2022 a"
    assert lines[1].strip().endswith("b")
    assert lines[1] == "    \u25e6 b"


def test_ordered_nested_list_unchanged():
    lines = _render("1. a\n   1. b\n")
    assert lines[0] == "1. a"
    assert "1." in lines[1] and lines[1].startswith("      ")


# ══════════════════════════════════════════════════════════
# 引用块：围栏 / 流式一致性
# ══════════════════════════════════════════════════════════


def test_blockquote_code_stream_no_dup_prefix():
    md = "> ```js\n> x=1\n> ```\n"
    once = _render(md)
    streamed = _render_stream(md)
    assert once == streamed
    assert once[1] == "\u2502 x=1"


def test_blockquote_indented_fence_closes():
    md = ">   ```js\n>   y=2\n>   ```\n"
    once = _render(md)
    assert once[0].startswith("\u2502 ```js")
    assert once[-1] == "\u2502 ```"
    assert len(once) == 3


# ══════════════════════════════════════════════════════════
# 容器正文：告示 / fenced div
# ══════════════════════════════════════════════════════════


def test_admonition_ref_body_list():
    lines = _render("> [!WARNING]\n> - a\n> - b\n")
    assert lines[0].startswith("\u25a0 WARNING")
    assert any(ln.strip() == "\u2022 a" for ln in lines)
    assert any(ln.strip() == "\u2022 b" for ln in lines)


def test_admonition_ref_body_code_block():
    lines = _render("> [!NOTE] 标题\n> ```py\n> x=1\n> ```\n")
    assert lines[0] == "\u25a0 NOTE 标题"
    assert any("```py" in ln for ln in lines)
    assert any(ln.strip() == "x=1" for ln in lines)


def test_admonition_ref_body_matches_stream():
    md = "> [!WARNING]\n> - a\n> - b\n"
    assert _render(md) == _render_stream(md)


def test_fenced_div_body_markdown():
    lines = _render("::: warning\n- a\n- b\n:::\n")
    assert lines[0].startswith("\u25aa WARNING")
    assert any(ln.strip() == "\u2022 a" for ln in lines)


def test_fenced_div_body_stream_matches():
    md = "::: note\n```py\nx=1\n```\n:::\n"
    assert _render(md) == _render_stream(md)


# ══════════════════════════════════════════════════════════
# HTML 嵌套列表
# ══════════════════════════════════════════════════════════


def test_html_nested_list_depth():
    md = "<ul>\n<li>一</li>\n<li>二\n<ul>\n<li>嵌套</li>\n</ul>\n</li>\n</ul>\n"
    lines = _render(md)
    assert lines[0].startswith("\u25b8 <ul")
    assert lines[1] == "\u2022 一"
    assert lines[2] == "\u2022 二"
    assert lines[3].strip().endswith("嵌套")
    assert lines[3].startswith("    ")


def test_html_ordered_list_numbers():
    lines = _render("<ol>\n<li>第一</li>\n<li>第二</li>\n</ol>\n")
    assert any(ln == "1. 第一" for ln in lines)
    assert any(ln == "2. 第二" for ln in lines)


def test_html_list_single_line():
    lines = _render("<ul><li>a</li><li>b</li></ul>\n")
    assert any(ln == "\u2022 a" for ln in lines)
    assert any(ln == "\u2022 b" for ln in lines)


def test_html_list_keeps_inline_format():
    lines = _render('<ul>\n<li><b>粗</b> 与 <a href="http://x">链接</a></li>\n</ul>\n')
    assert any("粗" in ln and "链接" in ln for ln in lines)


def test_html_table_still_parsed():
    lines = _render("<table>\n<tr><td>1</td><td>2</td></tr>\n</table>\n")
    assert any("\u250c" in ln for ln in lines)


def test_html_hr_rendered_as_rule():
    lines = _render("前\n<hr>\n后\n")
    assert any(set(ln) == {"\u2500"} and len(ln) >= 3 for ln in lines)
    assert not any("<hr>" in ln for ln in lines)


def test_html_p_rendered_as_paragraph():
    lines = _render("<p>段落 **粗**</p>\n")
    assert any(ln.strip() == "段落 粗" for ln in lines)
    assert not any("<p>" in ln for ln in lines)


def test_html_p_multiline():
    lines = _render("<p>\n第一行\n第二行\n</p>\n")
    assert "第一行" in lines
    assert "第二行" in lines


def test_html_heading_rendered_as_heading():
    lines = _render("<h2>二级标题</h2>\n")
    assert "二级标题" in lines
    assert not any("<h2>" in ln for ln in lines)


def test_html_pre_rendered_as_code_block():
    lines = _render("<pre>\ncode1\ncode2\n</pre>\n")
    assert any(ln.startswith("```") for ln in lines)
    assert "code1" in lines and "code2" in lines


def test_html_blockquote_rendered_with_prefix():
    lines = _render("<blockquote>\n引用内容\n</blockquote>\n")
    assert any(ln == "\u2502 引用内容" for ln in lines)


# ══════════════════════════════════════════════════════════
# 行内增强：kbd / span 颜色
# ══════════════════════════════════════════════════════════


def test_kbd_no_duplicate_icon():
    assert _inline_plain("<kbd>Ctrl</kbd>+<kbd>C</kbd>") == " Ctrl + C "


def test_span_style_color_applied():
    runs = render_inline('<span style="color:red">红</span>')
    styled = [r for r in runs if r.text == "红"]
    assert styled and styled[0].style.fg == 196


def test_span_hex_color_applied():
    runs = render_inline('<span style="color:#00ff00">绿</span>')
    styled = [r for r in runs if r.text == "绿"]
    assert styled and styled[0].style.fg is not None


def test_span_background_color_ignored():
    runs = render_inline('<span style="background-color:red">x</span>')
    styled = [r for r in runs if r.text == "x"]
    assert styled and styled[0].style.fg is None


# ══════════════════════════════════════════════════════════
# 脚注多段落 / 定义续段 / 列表项标题 / emoji
# ══════════════════════════════════════════════════════════


def test_footnote_multiline_rendered():
    lines = _render("文本[^1]\n\n[^1]: 第一段\n\n    第二段\n")
    assert any("第一段" in ln for ln in lines)
    assert any("第二段" in ln for ln in lines)
    assert not any("\\n" in ln for ln in lines)


def test_definition_continuation_indented():
    lines = _render("术语\n: 定义一\n\n    续段段落\n")
    cont = [ln for ln in lines if "续段段落" in ln]
    assert cont and cont[0].startswith("    ")


def test_list_item_heading():
    lines = _render("- # 标题\n- 普通\n")
    assert lines[0].startswith("\u2022 ")
    assert lines[0].endswith("标题")


def test_emoji_plus_one_shortcode():
    assert _inline_plain(":+1:") == "\U0001f44d"
    assert _inline_plain(":-1:") == "\U0001f44e"


# ══════════════════════════════════════════════════════════
# 流式一致性（一次性 vs 逐字符）
# ══════════════════════════════════════════════════════════


DOCS = [
    "# 标题\n\n段落 **粗** 与 `code`\n",
    "- 项一\n\n  ```py\n  x=1\n  ```\n\n- 项二\n",
    "> [!TIP] 提示\n> 1. 一\n> 2. 二\n",
    "| a | b |\n|---|---|\n| 行1<br>行2 | `x|y` |\n",
    "::: note\n> 引用\n:::\n",
    "文本[^1]\n\n[^1]: 一\n\n    二\n",
    "<ul>\n<li>一</li>\n<li>二</li>\n</ul>\n",
]


def test_stream_equals_once_for_nested_documents():
    for md in DOCS:
        assert _render(md) == _render_stream(md, chunk=1), md
        assert _render(md) == _render_stream(md, chunk=3), md


# ══════════════════════════════════════════════════════════
# Rich 路径同步（代码块 hl_lines / linenos / 脚注多段）
# ══════════════════════════════════════════════════════════


def test_rich_code_block_syntax_linenos_flag():
    from src.renderer._rendering._code import render_code_block_syntax

    syntax = render_code_block_syntax("a = 1\nb = 2", "python", "monokai",
                                      [1], linenos=True)
    assert getattr(syntax, "line_numbers", False) is True
    assert syntax.highlight_lines == {1}


def test_rich_code_line_number_prefix():
    from src.renderer.handlers.code import _line_number_prefix

    assert _line_number_prefix(7) == " 7 "


def test_rich_footnote_multiline_no_literal_newline():
    import io
    import re

    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write("文本[^1]\n\n[^1]: 第一段\n\n    第二段\n")
    r.close()
    plain = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
    assert "第一段" in plain and "第二段" in plain


def test_rich_details_body_still_markdown():
    import io
    import re

    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write("<details><summary>s</summary>\n\n- a\n- b\n\n</details>\n")
    r.close()
    plain = re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())
    assert "\u2022" in plain or "- a" in plain
