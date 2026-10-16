"""ANSI 流式 Markdown 语法/渲染增强（第五批）测试。

覆盖本轮改动：

  - 新增语法：行内属性 span ``[文本]{.class #id key=val}``（Pandoc）、
    引用 citation ``[@key]``（Pandoc）、图片/媒体行内属性
    ``{width= height= title=}``、链接标题圆括号形式 ``[t](url (title))``、
    Pandoc **grid table**（``+---+---+``）与 **simple table**（``-----  -----``）
  - 渲染错误修复：单行 HTML ``<table>``（caption 与 ``<tr>`` 同行）内容
    丢失；fenced 告示（``!!! type``）空行后正文被误判为缩进代码块；
    容器块（details / fenced div / 告示）尾部空行、空行缩进残留
  - 性能：段落预览行边界扫描的「无新完整行」快速路径；行列表公共前缀
    的 C 级整段比较快路径
"""

from __future__ import annotations

import time

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import AnsiLine
from src.renderer.ansi.inline import render_inline


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _render_chunked(src: str, size: int = 3, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _plain(runs) -> str:
    return "".join(r.text for r in runs)


# ══════════════════════════════════════════════════════════
# 行内属性 span ``[文本]{.class}``（Pandoc）
# ══════════════════════════════════════════════════════════


def test_span_class_color_style():
    runs = render_inline("[危险]{.red}")
    assert _plain(runs) == "危险"
    assert runs[0].style.fg == 196


def test_span_bold_class():
    runs = render_inline("[重点]{.bold}")
    assert _plain(runs) == "重点"
    assert runs[0].style.bold


def test_span_unknown_class_keeps_content():
    assert _plain(render_inline("[文本]{.nosuchclass}")) == "文本"


def test_span_id_and_key_value_ignored():
    assert _plain(render_inline("[锚点]{#sec lang=py}")) == "锚点"


def test_span_nested_inline_formatting():
    runs = render_inline("[**粗**体]{.green}")
    assert _plain(runs) == "粗体"
    # 嵌套粗体的 run 保留粗体，外层 class 颜色叠加
    bold_runs = [r for r in runs if r.text == "粗"]
    assert bold_runs and bold_runs[0].style.bold


def test_span_not_confused_with_color_syntax():
    # ``{color:red}`` 是既有语法（属性 token 含 ``:``，不含 ``.``/``#``/``=``）
    # → 不按 span 属性解析（``{color:red}`` 原样，不吞字符）
    assert _plain(render_inline("[x]{color:red}")) == "[x]{color:red}"
    # 完整的着色语法仍正常
    assert _plain(render_inline("{color:red}x{color}")) == "x"


def test_span_invalid_attr_falls_back():
    # ``{b}`` 无有效属性 → 原样（不吞字符）
    assert _plain(render_inline("[a]{b}")) == "[a]{b}"


def test_span_stream_render():
    lines = _render("[标题]{.yellow} 正文\n")
    assert lines == ["标题 正文"]


# ══════════════════════════════════════════════════════════
# 引用 citation ``[@key]``（Pandoc）
# ══════════════════════════════════════════════════════════


def test_citation_single():
    assert _plain(render_inline("文献 [@doe99]")) == "文献 [doe99]"


def test_citation_multiple_keys():
    assert _plain(render_inline("[@a; @b, p. 3]")) == "[a; b]"


def test_citation_suppress_author():
    assert _plain(render_inline("[-@doe]")) == "[-doe]"


def test_citation_stream_render():
    assert _render("见 [@key] 所述\n") == ["见 [key] 所述"]


# ══════════════════════════════════════════════════════════
# 图片 / 媒体行内属性
# ══════════════════════════════════════════════════════════


def test_image_attributes_width_height():
    lines = _render("![alt](img.png){width=100 height=50}\n")
    assert "=100x50" in lines[0]


def test_image_attribute_title():
    lines = _render("![alt](img.png){title=\"图说\"}\n")
    assert "\u56fe\u8bf4" in lines[0]          # 图说
    assert "{" not in lines[0]                 # 属性块不泄漏


def test_image_attributes_not_leaked():
    lines = _render("![a](b.png){width=10}\n")
    assert "{" not in "".join(lines)


# ══════════════════════════════════════════════════════════
# 链接标题圆括号形式
# ══════════════════════════════════════════════════════════


def test_link_paren_title():
    runs = render_inline("[t](http://x (title))")
    assert _plain(runs) == 't "title"'


def test_link_quote_title_still_works():
    assert _plain(render_inline("[t](http://x \"ti\")")) == 't "ti"'


# ══════════════════════════════════════════════════════════
# Pandoc grid table
# ══════════════════════════════════════════════════════════

_GRID = (
    "+-------+-------+\n"
    "| Head1 | Head2 |\n"
    "+=======+=======+\n"
    "| Cell1 | Cell2 |\n"
    "+-------+-------+\n"
)


def test_grid_table_basic():
    lines = _render(_GRID)
    assert lines[0].startswith("\u250c")       # ┌
    assert "Head1" in lines[1]
    assert any("Cell1" in ln for ln in lines)
    assert lines[-1].startswith("\u2514")      # └


def test_grid_table_alignment():
    lines = _render("+:--+--:+\n| a | b |\n+:==+==:+\n| 1 | 2 |\n+:--+--:+\n")
    assert lines[0].startswith("\u250c")
    assert "a" in lines[1] and "b" in lines[1]


def test_grid_table_multiline_cell():
    src = (
        "+-------+-------+\n"
        "| Head  | Head2 |\n"
        "+=======+=======+\n"
        "| Cell  | Cell2 |\n"
        "| cont  |       |\n"
        "+-------+-------+\n"
    )
    lines = _render(src)
    joined = "\n".join(lines)
    assert "Cell" in joined and "cont" in joined


def test_grid_table_stream_matches_once():
    assert _render_chunked(_GRID, size=1) == _render(_GRID)
    assert _render_chunked(_GRID, size=5) == _render(_GRID)


def test_grid_table_not_confused_with_hr_or_list():
    assert _render("---\n")[0].startswith("\u2500")      # 分隔线
    assert _render("+ item\n")[0].startswith("\u2022")   # 列表项


def test_grid_table_in_blockquote_gets_prefix():
    src = "> +---+---+\n> | a | b |\n> +===+===+\n> | 1 | 2 |\n> +---+---+\n"
    lines = _render(src)
    assert all(ln.startswith("\u2502 ") for ln in lines)   # │ 前缀
    assert "a" in lines[1] and "1" in lines[3]
    assert _render_chunked(src, size=4) == lines


def test_grid_table_in_list_gets_indent():
    src = "- item\n\n  +---+---+\n  | a | b |\n  +===+===+\n  | 1 | 2 |\n  +---+---+\n"
    lines = _render(src)
    body = [ln for ln in lines if "\u250c" in ln]
    assert body and body[0].startswith("  \u250c")          # 列表内容缩进 + ┌


# ══════════════════════════════════════════════════════════
# Pandoc simple table
# ══════════════════════════════════════════════════════════

_SIMPLE = (
    "  Right     Left     Center\n"
    "-------    ------    ------\n"
    "     12    12        12\n"
)


def test_simple_table_basic():
    lines = _render(_SIMPLE)
    assert lines[0].startswith("\u250c")
    assert "Right" in lines[1]
    assert any("12" in ln for ln in lines[3:])


def test_simple_table_stream_matches_once():
    assert _render_chunked(_SIMPLE, size=4) == _render(_SIMPLE)


def test_simple_table_single_dash_line_is_hr():
    lines = _render("标题\n\n---\n")
    assert any(ln.startswith("\u2500") for ln in lines)


# ══════════════════════════════════════════════════════════
# 渲染错误修复
# ══════════════════════════════════════════════════════════


def test_single_line_html_table_caption_and_rows():
    src = ('<table><caption>Cap</caption>'
           '<tr><th>H1</th><th>H2</th></tr>'
           '<tr><td>D1</td><td>D2</td></tr></table>\n')
    lines = _render(src)
    joined = "\n".join(lines)
    assert "H1" in joined and "D1" in joined       # 行数据不丢（修复前为空表）
    assert "Cap" in joined                          # 表注保留


def test_fenced_admonition_multiline_body_not_code_block():
    lines = _render("!!! note\n    l1\n\n    l2\n")
    joined = "\n".join(lines)
    assert "l1" in joined and "l2" in joined
    # 第二段不应渲染为代码块围栏
    assert "```" not in joined


def test_fenced_admonition_blank_kept_until_dedent():
    r = AnsiStreamRenderer(width=72)
    r.write('!!! note "标题"\n')
    r.write("    第一行\n")
    r.write("\n")            # 空行属正文 → 块仍开放（预览仍在）
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert preview and preview[0] == "\u25a0 NOTE \u6807\u9898"
    r.write("\u666e\u901a\u6bb5\u843d\n")   # 非缩进行 → 关闭并提交
    r.close()
    committed = [ln.plain for ln in r.take_lines()]
    assert any("\u25a0 NOTE" in ln for ln in committed)
    assert any("\u666e\u901a\u6bb5\u843d" in ln for ln in committed)


def test_container_blank_line_has_no_indent():
    lines = _render("<details>\n<summary>s</summary>\n\nl1\n\nl2\n\n</details>\n")
    blanks = [ln for ln in lines if ln.strip() == ""]
    assert blanks, "多段正文之间应有空行"
    for b in blanks:
        assert b == "", f"空行不应带缩进残留：{b!r}"


def test_container_no_trailing_blank():
    lines = _render("!!! note\n    body\n\n")
    assert lines[-1].strip() != ""      # 尾部空行不残留


def test_details_no_trailing_blank():
    lines = _render("<details>\n<summary>s</summary>\n\nbody\n\n</details>\n")
    assert lines[-1].strip() != ""


# ══════════════════════════════════════════════════════════
# AnsiLine.of("") 真空行
# ══════════════════════════════════════════════════════════


def test_ansiline_of_empty_has_no_runs():
    assert AnsiLine.of("").runs == []
    assert AnsiLine.of("x").runs != []


# ══════════════════════════════════════════════════════════
# 性能：段落预览增量与行前缀快路径
# ══════════════════════════════════════════════════════════


def test_paragraph_stream_scales_linearly_enough():
    """长段（多软换行）逐 chunk 写入应在合理时间内完成（无 O(n²) 爆炸）。"""
    text = "\n".join(f"第 {i} 行普通文本内容，无行内标记。" for i in range(400))
    r = AnsiStreamRenderer(width=100)
    t0 = time.perf_counter()
    for i in range(0, len(text), 3):
        r.write(text[i:i + 3])
    r.close()
    elapsed = time.perf_counter() - t0
    lines = [ln.plain for ln in r.take_lines()]
    assert len(lines) == 400
    assert elapsed < 5.0, f"400 行段落逐 3 字符写入耗时 {elapsed:.3f}s"


def test_paragraph_boundary_scanner_fast_path_equivalent():
    """行边界扫描快速路径（无新完整行）与全量结果一致。"""
    from src.renderer.ansi._line_delims import ParagraphBoundaryScanner

    a = ParagraphBoundaryScanner()
    text = "第一行正常\n第二行 **未闭合\n第三行** 结束\n"
    full = a.stable_line_count(text)
    b = ParagraphBoundaryScanner()
    # 逐字符累积调用（活动行增长 → 命中快速路径）
    last = 0
    for i in range(1, len(text) + 1):
        last = b.stable_line_count(text[:i])
    assert last == full
    # 有未闭合定界符时稳定行数应止于其所在行
    assert full <= 3
    assert a.stable_line_count(text) == full  # 幂等


# ══════════════════════════════════════════════════════════
# Rich 路径同步（新语法两渲染路径一致）
# ══════════════════════════════════════════════════════════


def test_rich_path_span_and_citation():
    from src.renderer.inline_renderer import render_inline as rich_inline

    assert rich_inline("[危险]{.red}").plain == "危险"
    assert rich_inline("文献 [@doe99]").plain == "文献 [doe99]"
    assert rich_inline("[t](http://x (title))").plain == 't "title"'
    assert "{" not in rich_inline("![a](b.png){width=10}").plain

