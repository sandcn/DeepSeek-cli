"""ANSI/TUI 流式 Markdown 渲染修复与性能（第十七批）回归测试。

本轮修复：

  - **行内数学跨段误配对**：``cost $5 and $10 total; $x+y$ ok`` 的首个金额
    ``$`` 不再与最后的公式闭合 ``$`` 配对（顶层 ``$`` 不满足闭合条件时立即
    放弃本次公式，内容中不允许未转义 ``$``）。
  - **裸 URL 吞尾部强调定界符**：``**https://x**`` / ``~~https://x~~`` 不再
    把 ``**`` / ``~~`` 计入 URL（否则强调语法整体失效、标记字面上屏）。
  - **Pandoc multiline table 列字符截断**：列内容右对齐/居中时可落入列段
    右侧，按「字段与列段区间重叠」归属列，``Center`` / ``Example`` 不再被
    切成 ``Cente`` / ``Exampl``。
  - **HTML 空元素同行后续标签泄漏**：``<input …> <input …>`` 的第二段标签
    按 HTML 语义渲染（不再原样上屏）；空元素同行文本按行内 Markdown 渲染。
  - **引用内列表内嵌套引用**：``> - a`` 后的 ``>   > b`` 正确输出
    ``│   │ b``；列表项内的标题 / 分隔线缩进到内容列并保留列表上下文。
  - **性能**：``wrap_line`` 等宽快路径的单 Run 行直接发射，免逐行二分 +
    段循环（80KB 长 ASCII 行换行成本显著下降）。
"""

from __future__ import annotations

import time

import pytest

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import AnsiLine, Run, wrap_line


def _ansi(src: str, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _stream(src: str, chunk: int = 1, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ══════════════════════════════════════════════════════════
# 行内数学：金额与公式混合
# ══════════════════════════════════════════════════════════

def test_inline_math_currency_then_formula():
    assert _ansi("cost $5 and $10 total; $x+y$ ok\n") == [
        "cost $5 and $10 total; x+y ok"]


def test_inline_math_currency_only_unchanged():
    assert _ansi("It costs $5 and $6 today.\n") == [
        "It costs $5 and $6 today."]


def test_inline_math_multiple_formulas():
    assert _ansi("$a$ $b$ text $c$ $d$\n") == ["a b text c d"]


def test_inline_math_unclosed_dollar_kept():
    assert _ansi("$5 and $x$ and $10 and $y$\n") == [
        "$5 and x and $10 and y"]


def test_inline_math_space_rules():
    assert _ansi("$ x $ and $x $\n") == ["$ x $ and $x $"]


def test_inline_math_stream_matches_once():
    src = "cost $5 and $10 total; $x+y$ ok and $z^2$\n"
    assert _stream(src) == _ansi(src)


def test_double_dollar_multiple_formulas_in_one_line():
    assert _ansi("$$x$$ and $$y$$\n") == ["x and y"]


def test_double_dollar_text_content_kept():
    lines = _ansi("$$\\text{a $$ b}$$\n")
    assert any("a $$ b" in ln for ln in lines)


def test_double_dollar_single_formula_still_block():
    lines = _ansi("$$x=1$$\n")
    assert lines[0].startswith("╭")
    assert any("x=1" in ln for ln in lines)


# ══════════════════════════════════════════════════════════
# Fenced Div 标题
# ══════════════════════════════════════════════════════════

def test_fenced_div_plain_title():
    assert _ansi("::: note Title\nbody\n:::\n")[0] == "▪ NOTE Title"


def test_fenced_div_attr_title():
    assert _ansi('::: {.note title="T"}\nbody\n:::\n')[0] == "▪ NOTE T"


def test_fenced_div_title_stream_matches_once():
    src = '::: {.warning title="Careful"}\nbody\n:::\n'
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 裸 URL 与尾部强调定界符
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src,expected", [
    ("**https://x**\n", "https://x"),
    ("*https://x*\n", "https://x"),
    ("~~https://x~~\n", "https://x"),
    ("==https://x==\n", "https://x"),
    ("||https://x||\n", "█████████"),
    ("**http://x/y**\n", "http://x/y"),
    ("**www.x.com**\n", "www.x.com"),
])
def test_bare_url_trailing_emphasis_markers(src, expected):
    assert _ansi(src) == [expected]


def test_bare_url_mid_underscore_preserved():
    assert _ansi("https://x/a_b and https://x/a_b_\n") == [
        "https://x/a_b and https://x/a_b_"]


def test_bare_url_trailing_punct_stays_visible():
    assert _ansi("see https://x.y/a, and (https://x.y/b).\n") == [
        "see https://x.y/a, and (https://x.y/b)."]


def test_bare_url_emphasis_stream_matches_once():
    src = "**https://x** and *https://y* and ~~https://z~~\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# Pandoc multiline table 列对齐
# ══════════════════════════════════════════════════════════

MULTILINE_TABLE = (
    "-------------------------------------\n"
    " Centered   Default           Right\n"
    " Header     Left    Center   Header\n"
    "---------- --------- ------ --------\n"
    " First      row     12.0   Example\n"
    "-------------------------------------\n"
)


def test_multiline_table_full_cell_content():
    lines = _ansi(MULTILINE_TABLE, width=80)
    joined = "\n".join(lines)
    assert "Center" in joined
    assert "Example" in joined
    assert "Cente\n" not in joined
    assert "Exampl\n" not in joined


def test_multiline_table_header_and_body_alignment():
    lines = _ansi(MULTILINE_TABLE, width=80)
    assert "Centered Header" in lines[1]
    assert "Default Left" in lines[1]
    assert "Right Header" in lines[1]
    assert "First" in lines[3]
    assert "12.0" in lines[3]
    assert "Example" in lines[3]


def test_multiline_table_existing_sample():
    src = (
        "-------------------------------------------------------------\n"
        " Name     Age\n"
        "-------- -------\n"
        " Alice     30\n"
        "\n"
        " Bob       40\n"
        "-------------------------------------------------------------\n"
    )
    lines = _ansi(src)
    assert any("Alice" in ln and "30" in ln for ln in lines)
    assert any("Bob" in ln and "40" in ln for ln in lines)


def test_multiline_table_stream_matches_once():
    assert _stream(MULTILINE_TABLE, chunk=7, width=80) == _ansi(
        MULTILINE_TABLE, width=80)


def test_multiline_table_alignment_markers():
    src = (
        "------------------------------\n"
        " Centered   Default   Right\n"
        " Header     Left      Header\n"
        ":--------- --------- ------:\n"
        " First      row       12.0\n"
        "------------------------------\n"
    )
    lines = _ansi(src, width=70)
    # 右对齐列：数值靠右；左对齐列：文本靠左
    assert "12.0 │" in lines[3]
    assert "│ First" in lines[3]
    assert "Header" in lines[1]


def test_simple_table_alignment_markers():
    src = (
        " Right     Left     Center\n"
        "------:    :------  :-----:\n"
        "     12         12       12\n"
    )
    lines = _ansi(src, width=60)
    assert lines[1].count("Right") == 1
    assert "Left" in lines[1]
    assert "Center" in lines[1]


def test_multiline_table_alignment_stream_matches_once():
    src = (
        "------------------------------\n"
        " Centered   Default   Right\n"
        " Header     Left      Header\n"
        ":--------- --------- ------:\n"
        " First      row       12.0\n"
        "------------------------------\n"
    )
    assert _stream(src, chunk=5, width=70) == _ansi(src, width=70)


# ══════════════════════════════════════════════════════════
# HTML 空元素同行后续标签 / 行内 Markdown
# ══════════════════════════════════════════════════════════

def test_html_void_consecutive_inputs():
    assert _ansi('<input type="checkbox" checked> <input type="text">\n') == [
        "  ☑ ", "  ▸ <input text>"]


def test_html_void_tail_inline_markdown():
    assert _ansi("<input checked> **done**\n") == ["  ▸ <input text> done"]


def test_html_void_img_tail_inline_markdown():
    assert _ansi('<img src="a.png"> *cap*\n') == ["  ▣ 图片 (a.png) cap"]


def test_html_void_stream_matches_once():
    src = '<input type="checkbox" checked> <input type="text">\n'
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 引用 / 列表嵌套前缀
# ══════════════════════════════════════════════════════════

def test_quote_list_nested_quote_prefix():
    assert _ansi("> - a\n>   > b\n") == ["│ • a", "│   │ b"]


def test_quote_list_nested_quote_three_levels():
    assert _ansi("> - a\n>   > b\n>   > > c\n") == [
        "│ • a", "│   │ b", "│   │ │ c"]


def test_quote_list_heading_prefix():
    assert _ansi("> - a\n>   # H\n") == ["│ • a", "│   H"]


def test_quote_list_heading_keeps_list_context():
    assert _ansi("> - a\n>   # H\n>   text\n") == [
        "│ • a", "│   H", "│   text"]


def test_list_heading_indent_and_continuation():
    assert _ansi("- a\n\n  # H\n  text\n") == ["• a", "", "  H", "  text"]


def test_list_hr_indent():
    lines = _ansi("- a\n\n  ---\n\n- b\n")
    assert lines[0] == "• a"
    assert lines[2].startswith("  ─")
    assert lines[-1] == "• b"


def test_quote_list_nested_quote_stream_matches_once():
    src = "> - a\n>   > b\n>   > > c\n> - d\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 流式预览与提交一致
# ══════════════════════════════════════════════════════════

def _preview(src: str, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    return [ln.plain for ln in r.take_preview_lines()]


def test_quote_list_nested_quote_preview():
    assert _preview("> - a\n>   > b") == ["│   │ b"]


def test_quote_list_heading_preview():
    assert _preview("> - a\n>   # H") == ["│   H"]


def test_quote_list_text_preview():
    assert _preview("> - a\n>   text") == ["│   text"]


def test_multiline_table_preview_matches_commit():
    src = MULTILINE_TABLE.rstrip("\n")
    assert _preview(src, width=80) == _ansi(src + "\n", width=80)


def test_math_currency_preview_matches_commit():
    src = "cost $5 and $10 total; $x+y$ ok"
    assert _preview(src) == _ansi(src + "\n")


# ══════════════════════════════════════════════════════════
# 换行性能：单 Run 等宽快路径
# ══════════════════════════════════════════════════════════

def test_wrap_uniform_single_span_matches_multi_span():
    text = "lorem ipsum dolor " * 200
    single = AnsiLine([Run(text, None)])
    multi = AnsiLine([
        Run(text[:500], None),
        Run(text[500:1500], None),
        Run(text[1500:], None),
    ])
    assert [ln.plain for ln in wrap_line(single, 120)] == [
        ln.plain for ln in wrap_line(multi, 120)]


def test_wrap_uniform_long_ascii_budget():
    line = AnsiLine([Run("lorem ipsum dolor " * 4096, None)])
    wrap_line(line, 120)  # 预热
    t0 = time.perf_counter()
    for _ in range(3):
        wrap_line(line, 120)
    avg = (time.perf_counter() - t0) / 3
    assert avg < 0.02, avg


def test_wrap_uniform_long_cjk_budget():
    line = AnsiLine([Run("中文内容" * 4096, None)])
    wrap_line(line, 120)
    t0 = time.perf_counter()
    for _ in range(3):
        wrap_line(line, 120)
    avg = (time.perf_counter() - t0) / 3
    assert avg < 0.02, avg


def test_wrap_uniform_preserves_width_and_word_boundary():
    text = "alpha beta gamma delta epsilon " * 20
    line = AnsiLine([Run(text, None)])
    rows = wrap_line(line, 40)
    assert all(ln.width <= 40 for ln in rows)
    assert "".join(ln.plain for ln in rows).replace(" ", "") == (
        text.replace(" ", ""))
